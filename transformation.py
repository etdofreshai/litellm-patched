import json
from typing import Any, Optional

from litellm.constants import STREAM_SSE_DONE_STRING
from litellm.exceptions import AuthenticationError
from litellm.litellm_core_utils.core_helpers import process_response_headers
from litellm.litellm_core_utils.llm_response_utils.convert_dict_to_response import (
    _safe_convert_created_field,
)
from litellm.llms.openai.common_utils import OpenAIError
from litellm.llms.openai.responses.transformation import OpenAIResponsesAPIConfig
from litellm.types.llms.openai import (
    ResponsesAPIResponse,
    ResponsesAPIStreamEvents,
)
from litellm.types.router import GenericLiteLLMParams
from litellm.types.utils import LlmProviders
from litellm.utils import CustomStreamWrapper

from ..authenticator import Authenticator
from ..common_utils import (
    CHATGPT_API_BASE,
    GetAccessTokenError,
    ensure_chatgpt_session_id,
    get_chatgpt_default_headers,
    get_chatgpt_default_instructions,
)


class ChatGPTResponsesAPIConfig(OpenAIResponsesAPIConfig):
    def __init__(self) -> None:
        super().__init__()
        self.authenticator = Authenticator()

    @property
    def custom_llm_provider(self) -> LlmProviders:
        return LlmProviders.CHATGPT

    def validate_environment(
        self,
        headers: dict,
        model: str,
        litellm_params: Optional[GenericLiteLLMParams],
    ) -> dict:
        try:
            access_token = self.authenticator.get_access_token()
        except GetAccessTokenError as e:
            raise AuthenticationError(
                model=model,
                llm_provider="chatgpt",
                message=str(e),
            )

        account_id = self.authenticator.get_account_id()
        session_id = ensure_chatgpt_session_id(litellm_params)
        default_headers = get_chatgpt_default_headers(
            access_token, account_id, session_id
        )
        return {**default_headers, **headers}

    def transform_responses_api_request(
        self,
        model: str,
        input: Any,
        response_api_optional_request_params: dict,
        litellm_params: GenericLiteLLMParams,
        headers: dict,
    ) -> dict:
        request = super().transform_responses_api_request(
            model,
            input,
            response_api_optional_request_params,
            litellm_params,
            headers,
        )
        base_instructions = get_chatgpt_default_instructions()
        existing_instructions = request.get("instructions")
        if existing_instructions:
            if base_instructions not in existing_instructions:
                request["instructions"] = (
                    f"{base_instructions}\n\n{existing_instructions}"
                )
        else:
            request["instructions"] = base_instructions
        request["store"] = False
        request["stream"] = True
        include = list(request.get("include") or [])
        if "reasoning.encrypted_content" not in include:
            include.append("reasoning.encrypted_content")
        request["include"] = include

        allowed_keys = {
            "model",
            "input",
            "instructions",
            "stream",
            "store",
            "include",
            "tools",
            "tool_choice",
            "reasoning",
            "previous_response_id",
            "truncation",
        }

        return {k: v for k, v in request.items() if k in allowed_keys}

    def transform_response_api_response(
        self,
        model: str,
        raw_response: Any,
        logging_obj: Any,
    ):
        content_type = (raw_response.headers or {}).get("content-type", "")
        body_text = raw_response.text or ""
        if "text/event-stream" not in content_type.lower():
            trimmed_body = body_text.lstrip()
            if not (
                trimmed_body.startswith("event:")
                or trimmed_body.startswith("data:")
                or "\nevent:" in body_text
                or "\ndata:" in body_text
            ):
                return super().transform_response_api_response(
                    model=model,
                    raw_response=raw_response,
                    logging_obj=logging_obj,
                )

        logging_obj.post_call(
            original_response=raw_response.text,
            additional_args={"complete_input_dict": {}},
        )

        completed_response = None
        completed_payload: Optional[dict] = None
        error_message = None
        # Aggregate items as they stream in. The codex backend sometimes
        # returns response.completed with output=[]; we rebuild output from
        # output_item.added + output_text.delta + content_part.added events.
        items_by_id: dict = {}
        item_order: list = []

        def ensure_item(item: dict) -> dict:
            iid = item.get("id")
            if not iid:
                return item
            if iid not in items_by_id:
                items_by_id[iid] = json.loads(json.dumps(item))
                item_order.append(iid)
                # Make sure content is a list we can append to
                if items_by_id[iid].get("type") == "message":
                    items_by_id[iid].setdefault("content", [])
            return items_by_id[iid]

        for chunk in body_text.splitlines():
            stripped_chunk = CustomStreamWrapper._strip_sse_data_from_chunk(chunk)
            if not stripped_chunk:
                continue
            stripped_chunk = stripped_chunk.strip()
            if not stripped_chunk:
                continue
            if stripped_chunk == STREAM_SSE_DONE_STRING:
                break
            try:
                parsed_chunk = json.loads(stripped_chunk)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed_chunk, dict):
                continue
            event_type = parsed_chunk.get("type")

            if event_type == "response.output_item.added":
                item = parsed_chunk.get("item")
                if isinstance(item, dict):
                    ensure_item(item)
            elif event_type == "response.output_item.done":
                item = parsed_chunk.get("item")
                if isinstance(item, dict) and item.get("id"):
                    items_by_id[item["id"]] = json.loads(json.dumps(item))
            elif event_type == "response.content_part.added":
                iid = parsed_chunk.get("item_id")
                idx = parsed_chunk.get("content_index")
                part = parsed_chunk.get("part")
                if iid and isinstance(idx, int) and isinstance(part, dict) and iid in items_by_id:
                    contents = items_by_id[iid].setdefault("content", [])
                    while len(contents) <= idx:
                        contents.append({"type": "output_text", "text": "", "annotations": []})
                    contents[idx] = json.loads(json.dumps(part))
                    contents[idx].setdefault("text", "")
            elif event_type == "response.output_text.delta":
                iid = parsed_chunk.get("item_id")
                idx = parsed_chunk.get("content_index", 0)
                delta = parsed_chunk.get("delta", "")
                if iid and iid in items_by_id and isinstance(delta, str):
                    contents = items_by_id[iid].setdefault("content", [])
                    while len(contents) <= idx:
                        contents.append({"type": "output_text", "text": "", "annotations": []})
                    cur = contents[idx]
                    if not isinstance(cur, dict):
                        cur = {"type": "output_text", "text": "", "annotations": []}
                        contents[idx] = cur
                    cur["text"] = (cur.get("text") or "") + delta
            elif event_type == "response.output_text.done":
                iid = parsed_chunk.get("item_id")
                idx = parsed_chunk.get("content_index", 0)
                final_text = parsed_chunk.get("text")
                if iid and iid in items_by_id and isinstance(final_text, str):
                    contents = items_by_id[iid].setdefault("content", [])
                    while len(contents) <= idx:
                        contents.append({"type": "output_text", "text": "", "annotations": []})
                    cur = contents[idx]
                    if not isinstance(cur, dict):
                        cur = {"type": "output_text", "text": "", "annotations": []}
                        contents[idx] = cur
                    cur["text"] = final_text

            if event_type == ResponsesAPIStreamEvents.RESPONSE_COMPLETED:
                response_payload = parsed_chunk.get("response")
                if isinstance(response_payload, dict):
                    completed_payload = dict(response_payload)
                break
            if event_type in (
                ResponsesAPIStreamEvents.RESPONSE_FAILED,
                ResponsesAPIStreamEvents.ERROR,
            ):
                error_obj = parsed_chunk.get("error") or (
                    parsed_chunk.get("response") or {}
                ).get("error")
                if error_obj is not None:
                    if isinstance(error_obj, dict):
                        error_message = error_obj.get("message") or str(error_obj)
                    else:
                        error_message = str(error_obj)

        if completed_payload is not None:
            existing_output = completed_payload.get("output")
            if not existing_output and item_order:
                completed_payload["output"] = [items_by_id[i] for i in item_order]
            if "created_at" in completed_payload:
                completed_payload["created_at"] = _safe_convert_created_field(
                    completed_payload["created_at"]
                )
            try:
                completed_response = ResponsesAPIResponse(**completed_payload)
            except Exception:
                completed_response = ResponsesAPIResponse.model_construct(
                    **completed_payload
                )

        if completed_response is None:
            raise OpenAIError(
                message=error_message or raw_response.text,
                status_code=raw_response.status_code,
            )

        raw_headers = dict(raw_response.headers)
        processed_headers = process_response_headers(raw_headers)
        if not hasattr(completed_response, "_hidden_params"):
            setattr(completed_response, "_hidden_params", {})
        completed_response._hidden_params["additional_headers"] = processed_headers
        completed_response._hidden_params["headers"] = raw_headers
        return completed_response

    def get_complete_url(
        self,
        api_base: Optional[str],
        litellm_params: dict,
    ) -> str:
        api_base = api_base or self.authenticator.get_api_base() or CHATGPT_API_BASE
        api_base = api_base.rstrip("/")
        return f"{api_base}/responses"

    def supports_native_websocket(self) -> bool:
        """ChatGPT does not support native WebSocket for Responses API"""
        return False
