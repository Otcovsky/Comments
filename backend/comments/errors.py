from graphql import GraphQLError


class ApiError(GraphQLError):
    """An error the client expects: its code and fields go to `extensions` (docs/api.md)."""

    def __init__(self, code, fields=None):
        extensions = {"code": code}
        if fields:
            extensions["fields"] = fields
        super().__init__("Validation failed" if fields else code, extensions=extensions)
