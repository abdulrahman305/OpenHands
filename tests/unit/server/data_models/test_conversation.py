import json
from contextlib import contextmanager
from datetime import datetime, timezone
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from openhands.integrations.service_types import (
    AuthenticationError,
    CreateMicroagent,
    ProviderType,
    SuggestedTask,
    TaskType,
)
from openhands.runtime.runtime_status import RuntimeStatus
from openhands.server.data_models.conversation_info import ConversationInfo
from openhands.server.data_models.conversation_info_result_set import (
    ConversationInfoResultSet,
)
from openhands.server.routes.manage_conversations import (
    ConversationResponse,
    InitSessionRequest,
    delete_conversation,
    get_conversation,
    new_conversation,
    search_conversations,
)
from openhands.server.routes.manage_conversations import app as conversation_app
from openhands.server.types import LLMAuthenticationError, MissingSettingsError
from openhands.server.user_auth.user_auth import AuthType
from openhands.storage.data_models.conversation_metadata import (
    ConversationMetadata,
    ConversationTrigger,
)
from openhands.storage.data_models.conversation_status import ConversationStatus
from openhands.storage.locations import get_conversation_metadata_filename
from openhands.storage.memory import InMemoryFileStore


@contextmanager
def _patch_store():
    file_store = InMemoryFileStore()
    file_store.write(
        get_conversation_metadata_filename('some_conversation_id'),
        json.dumps(
            {
                'title': 'Some ServerConversation',
                'selected_repository': 'foobar',
                'conversation_id': 'some_conversation_id',
                'user_id': '12345',
                'created_at': '2025-01-01T00:00:00+00:00',
                'last_updated_at': '2025-01-01T00:01:00+00:00',
            }
        ),
    )
    with patch(
        'openhands.storage.conversation.file_conversation_store.get_file_store',
        MagicMock(return_value=file_store),
    ):
        with patch(
            'openhands.server.routes.manage_conversations.conversation_manager.file_store',
            file_store,
        ):
            yield


@pytest.fixture
def test_client():
    """Create a test client for the settings API."""
    app = FastAPI()
    app.include_router(conversation_app)
    return TestClient(app)


def create_new_test_conversation(
    test_request: InitSessionRequest, auth_type: AuthType | None = None
):
    # Create a mock UserSecrets object with the required custom_secrets attribute
    mock_user_secrets = MagicMock()
    mock_user_secrets.custom_secrets = MappingProxyType({})

    return new_conversation(
        data=test_request,
        user_id='test_user',
        provider_tokens=MappingProxyType({'github': 'token123'}),
        user_secrets=mock_user_secrets,
        auth_type=auth_type,
    )


@pytest.fixture
def provider_handler_mock():
    with patch(
        'openhands.server.routes.manage_conversations.ProviderHandler'
    ) as mock_cls:
        mock_instance = MagicMock()
        mock_instance.verify_repo_provider = AsyncMock(return_value=ProviderType.GITHUB)
        mock_cls.return_value = mock_instance
        yield mock_instance


@pytest.mark.asyncio
async def test_search_conversations():
    with _patch_store():
        with patch(
            'openhands.server.routes.manage_conversations.config'
        ) as mock_config:
            mock_config.conversation_max_age_seconds = 864000  # 10 days
            with patch(
                'openhands.server.routes.manage_conversations.conversation_manager'
            ) as mock_manager:

                async def mock_get_running_agent_loops(*args, **kwargs):
                    return set()

                async def mock_get_connections(*args, **kwargs):
                    return {}

                async def get_agent_loop_info(*args, **kwargs):
                    return []

                mock_man