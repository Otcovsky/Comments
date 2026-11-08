"""The GraphQL schema from docs/api.md. It stays flat: every resolver makes its own queries,
and a Comment holds plain values, so resolving its fields costs no SQL."""
import enum
import logging

import redis
import strawberry
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from graphql import GraphQLError
from strawberry.extensions import MaskErrors, MaxAliasesLimiter, MaxTokensLimiter, ParserCache, ValidationCache
from strawberry.file_uploads import Upload
from strawberry.types import Info

from . import attachments, captcha, live, queries, ratelimit, shared, storage, tokens, validation
from .errors import ApiError
from .models import Comment as CommentModel
from .pools import in_db_thread, in_file_thread

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = live.MAX_PAGE_SIZE


@strawberry.enum
class CommentOrdering(enum.Enum):
    CREATED_AT_DESC = "CREATED_AT_DESC"
    CREATED_AT_ASC = "CREATED_AT_ASC"
    USERNAME_ASC = "USERNAME_ASC"
    USERNAME_DESC = "USERNAME_DESC"
    EMAIL_ASC = "EMAIL_ASC"
    EMAIL_DESC = "EMAIL_DESC"


@strawberry.enum
class AttachmentType(enum.Enum):
    IMAGE = CommentModel.AttachmentType.IMAGE.value
    TEXT = CommentModel.AttachmentType.TEXT.value


@strawberry.type
class Comment:
    id: strawberry.ID
    parent_id: strawberry.ID | None
    root_id: strawberry.ID | None
    username: str
    email: str
    home_page: str
    text: str
    attachment: str | None
    attachment_type: AttachmentType | None
    created_at: str
    reply_count: int

    @classmethod
    def from_row(cls, row):
        return cls(
            id=strawberry.ID(str(row["id"])),
            parent_id=_optional_id(row["parent_id"]),
            root_id=_optional_id(row["root_id"]),
            username=row["username"],
            email=row["email"],
            home_page=row["author__home_page"],
            text=row["text"],
            attachment=f"/media/{row['attachment']}" if row["attachment"] else None,
            attachment_type=AttachmentType(row["attachment_type"]) if row["attachment_type"] else None,
            created_at=row["created_at"].isoformat(),
            reply_count=row["reply_count"],
        )


def _optional_id(value):
    return None if value is None else strawberry.ID(str(value))


@strawberry.type
class CommentPage:
    count: int
    results: list[Comment]


@strawberry.type
class Captcha:
    key: str
    image: str


@strawberry.type
class CreateCommentResult:
    comment: Comment
    token: str | None
    token_expires_in: int | None


@strawberry.input
class CommentInput:
    username: str
    email: str
    home_page: str
    text: str
    parent_id: strawberry.ID | None = None
    captcha_key: str | None = None
    captcha_value: str | None = None


def _client_address(request):
    # Nginx sets the header; the backend's port is closed to everyone else.
    return request.headers.get("X-Real-IP") or request.META.get("REMOTE_ADDR")


async def _check_rate(limiter, request):
    address = _client_address(request)
    if not ratelimit.is_exempt(address) and not await limiter.allow(address):
        raise ApiError("RATE_LIMITED", {"rateLimit": "Слишком много запросов. Подождите минуту."})


async def _check_captcha(input, username, email, authorization):
    """Returns (error for the user or None, whether a captcha was solved in this request)."""
    if input.captcha_key is None:
        if tokens.allows(authorization, username, email):
            return None, False
        return "Введите код с картинки.", False
    # Spent whatever else is wrong with the request: every attempt costs a captcha.
    try:
        solved = await captcha.store.check(input.captcha_key, input.captcha_value or "")
    except redis.RedisError as error:
        shared.warn(logger, "Captchas cannot be checked, Redis failed", error)
        return "Не удалось проверить код. Обновите картинку и попробуйте ещё раз.", False
    if solved:
        return None, True
    return "Неверный или устаревший код.", False


def _store_attachment(upload):
    file = attachments.process(upload)
    return storage.put(file), file.kind


async def _save_attachment(upload):
    try:
        return await in_file_thread(_store_attachment, upload)
    except attachments.AttachmentError as error:
        raise ApiError("VALIDATION", {"attachment": str(error)})
    except (BotoCoreError, ClientError):
        logger.exception("Could not store an attachment")
        raise ApiError("VALIDATION", {"attachment": "Не удалось сохранить файл, попробуйте ещё раз."})


@strawberry.type
class Query:
    @strawberry.field
    async def comments(self, page: int, page_size: int, ordering: CommentOrdering) -> CommentPage:
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ApiError("INVALID_PAGE_SIZE")
        if page < 1:
            raise ApiError("PAGE_NOT_FOUND")
        count, rows = await in_db_thread(queries.comment_page, page, page_size, ordering.value)
        if not rows and page > 1:
            raise ApiError("PAGE_NOT_FOUND")
        return CommentPage(count=count, results=[Comment.from_row(row) for row in rows])

    @strawberry.field
    async def replies(self, parent_id: strawberry.ID) -> list[Comment]:
        comment_id = validation.parse_id(parent_id)
        if comment_id is None:
            return []
        return [Comment.from_row(row) for row in await in_db_thread(queries.replies, comment_id)]

    @strawberry.field
    def preview_text(self, text: str) -> str:
        return validation.preview_text(text)


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def issue_captcha(self, info: Info) -> Captcha:
        await _check_rate(ratelimit.captchas, info.context.request)
        key, image = await captcha.issue()
        return Captcha(key=key, image=image)

    @strawberry.mutation
    async def create_comment(
        self, info: Info, input: CommentInput, attachment: Upload | None = None
    ) -> CreateCommentResult:
        request = info.context.request
        # Before the captcha check, so that a refused request does not spend the captcha.
        await _check_rate(ratelimit.comments, request)
        username, email, home_page = input.username.strip(), input.email.strip(), input.home_page.strip()
        errors = {
            field: message
            for field, message in (
                ("username", validation.check_username(username)),
                ("email", validation.check_email(email)),
                ("homePage", validation.check_home_page(home_page)),
            )
            if message
        }
        text = ""
        try:
            text = validation.clean_text(input.text)
        except ValueError as error:
            errors["text"] = str(error)

        captcha_error, captcha_solved = await _check_captcha(
            input, username, email, request.headers.get("Authorization")
        )
        if captcha_error:
            errors["captchaValue"] = captcha_error

        parent = None
        if input.parent_id is not None:
            parent_id = validation.parse_id(input.parent_id)
            parent = await in_db_thread(queries.find_parent, parent_id) if parent_id is not None else None
            if parent is None:
                errors["parentId"] = "Комментарий, на который вы отвечаете, не найден."
        if errors:
            raise ApiError("VALIDATION", errors)

        path, kind = await _save_attachment(attachment) if attachment else ("", CommentModel.AttachmentType.NONE)

        row = await in_db_thread(
            queries.save_comment,
            username=username,
            email=email,
            home_page=home_page,
            parent=parent,
            text=text,
            attachment=path,
            attachment_type=kind,
            ip_address=_client_address(request),
            user_agent=request.headers.get("User-Agent", "")[:255],
        )
        live.comment_created_later(row, parent)

        return CreateCommentResult(
            comment=Comment.from_row(row),
            token=tokens.issue(username, email) if captcha_solved else None,
            token_expires_in=settings.AUTHOR_TOKEN_TTL if captcha_solved else None,
        )


def _is_unexpected(error: GraphQLError):
    """Bugs and outages reach the client without details; query errors and ApiError are meant for it."""
    return error.original_error is not None and not isinstance(error.original_error, ApiError)


class CommentsSchema(strawberry.Schema):
    def process_errors(self, errors, execution_context=None):
        # Expected errors are part of the API, not something to log.
        unexpected = [error for error in errors if _is_unexpected(error)]
        if unexpected:
            super().process_errors(unexpected, execution_context)


schema = CommentsSchema(
    query=Query,
    mutation=Mutation,
    extensions=[
        # The client sends the same few query strings, so this skips parsing and validation.
        ParserCache(maxsize=64),
        ValidationCache(maxsize=64),
        MaxTokensLimiter(max_token_count=1000),
        # One request must not issue a hundred captchas.
        MaxAliasesLimiter(max_alias_count=5),
        MaskErrors(should_mask_error=_is_unexpected),
    ],
)
