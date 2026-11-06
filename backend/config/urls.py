from django.urls import path
from strawberry.django.views import AsyncGraphQLView

from comments.schema import schema

urlpatterns = [
    path(
        "graphql/",
        AsyncGraphQLView.as_view(
            schema=schema,
            graphql_ide=None,
            allow_queries_via_get=False,
            multipart_uploads_enabled=True,
        ),
    ),
]
