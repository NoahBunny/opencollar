"""Tests for shared/focuslock_llm.py — Ollama photo verification + task generation."""

import json
from unittest.mock import MagicMock, patch

from focuslock_llm import (
    DEFAULT_TEXT_MODEL,
    DEFAULT_VISION_MODEL,
    generate_task_with_llm,
    verify_photo_with_llm,
)


def _ollama_response(response_text: str) -> MagicMock:
    """Mock urlopen response shaped like Ollama's /api/generate result."""
    resp = MagicMock()
    resp.read.return_value = json.dumps({"response": response_text}).encode()
    return resp


# ── verify_photo_with_llm ──


class TestVerifyPhotoEarlyExit:
    def test_empty_photo_returns_failure_without_http_call(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            result = verify_photo_with_llm("", "Do 20 pushups")
        assert result == {"ok": False, "passed": False, "reason": "No photo provided"}
        assert urlopen.call_count == 0


class TestVerifyPhotoJsonResponse:
    def test_happy_path_passed(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"passed": true, "reason": "Person clearly doing pushups"}')
            result = verify_photo_with_llm("base64data", "Do 20 pushups")
        assert result == {"ok": True, "passed": True, "reason": "Person clearly doing pushups"}

    def test_happy_path_failed(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"passed": false, "reason": "Photo shows empty room"}')
            result = verify_photo_with_llm("b64", "Clean kitchen")
        assert result == {"ok": True, "passed": False, "reason": "Photo shows empty room"}

    def test_json_extracted_from_surrounding_text(self):
        """Ollama models often pad JSON with prose; extractor finds {...} block."""
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response(
                'Here is my evaluation:\n{"passed": true, "reason": "ok"}\n\nThank you.'
            )
            result = verify_photo_with_llm("b64", "task")
        assert result["ok"] is True
        assert result["passed"] is True

    def test_missing_passed_field_defaults_false(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"reason": "no verdict"}')
            result = verify_photo_with_llm("b64", "task")
        assert result == {"ok": True, "passed": False, "reason": "no verdict"}

    def test_missing_reason_field_defaults_string(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"passed": true}')
            result = verify_photo_with_llm("b64", "task")
        assert result == {"ok": True, "passed": True, "reason": "No reason given"}


class TestVerifyPhotoFallbackKeywords:
    """When response contains no parseable JSON, fallback keyword detection kicks in."""

    def test_keyword_pass_detected(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response("The task appears to have been completed.")
            # No braces at all → JSON extractor's start/end check fails (start = -1)
            result = verify_photo_with_llm("b64", "task")
        assert result["ok"] is True
        assert result["passed"] is True
        assert "completed" in result["reason"]

    def test_keyword_yes_detected(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response("yes, this looks fine")
            result = verify_photo_with_llm("b64", "task")
        assert result["passed"] is True

    def test_no_keywords_means_failed(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response("Cannot determine from image.")
            result = verify_photo_with_llm("b64", "task")
        assert result["ok"] is True
        assert result["passed"] is False

    def test_malformed_json_inside_braces_falls_back(self):
        """JSONDecodeError inside the {...} block → fallback to keyword scan."""
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response("{not valid json} but task completed")
            result = verify_photo_with_llm("b64", "task")
        assert result["ok"] is True
        assert result["passed"] is True  # "completed" keyword wins the fallback

    def test_response_text_truncated_to_200_chars(self):
        long_response = "no verdict " * 100  # > 200 chars
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response(long_response)
            result = verify_photo_with_llm("b64", "task")
        assert len(result["reason"]) == 200


class TestVerifyPhotoEvidenceCallback:
    def test_callback_fired_on_pass(self):
        evidence_calls = []
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"passed": true, "reason": "good"}')
            verify_photo_with_llm(
                "b64",
                "Pet the dog",
                on_evidence=lambda text, etype: evidence_calls.append((text, etype)),
            )
        assert len(evidence_calls) == 1
        text, etype = evidence_calls[0]
        assert "PASSED" in text
        assert "Pet the dog" in text
        assert "good" in text
        assert etype == "photo task passed"

    def test_callback_fired_on_fail(self):
        evidence_calls = []
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"passed": false, "reason": "bad"}')
            verify_photo_with_llm(
                "b64",
                "task",
                on_evidence=lambda text, etype: evidence_calls.append((text, etype)),
            )
        assert evidence_calls[0][1] == "photo task failed"
        assert "FAILED" in evidence_calls[0][0]

    def test_no_callback_does_not_raise(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"passed": true, "reason": "ok"}')
            result = verify_photo_with_llm("b64", "task", on_evidence=None)
        assert result["passed"] is True

    def test_callback_not_fired_on_fallback_keyword_path(self):
        """Evidence callback only fires when JSON parses — fallback keyword scan skips it."""
        evidence_calls = []
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response("yes, completed")
            verify_photo_with_llm(
                "b64",
                "task",
                on_evidence=lambda text, etype: evidence_calls.append((text, etype)),
            )
        assert evidence_calls == []


class TestVerifyPhotoErrors:
    def test_http_error_returns_failure(self):
        with patch("focuslock_llm.urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = verify_photo_with_llm("b64", "task")
        assert result == {"ok": False, "passed": False, "reason": "connection refused"}

    def test_response_decode_error_returns_failure(self):
        bad_resp = MagicMock()
        bad_resp.read.return_value = b"not json"
        with patch("focuslock_llm.urllib.request.urlopen", return_value=bad_resp):
            result = verify_photo_with_llm("b64", "task")
        assert result["ok"] is False
        assert result["passed"] is False


class TestVerifyPhotoPayload:
    def test_default_url_and_model_used(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"passed": true}')
            verify_photo_with_llm("b64data", "task")
        req = urlopen.call_args.args[0]
        assert req.full_url == "http://localhost:11434/api/generate"
        body = json.loads(req.data.decode())
        assert body["model"] == DEFAULT_VISION_MODEL
        assert body["images"] == ["b64data"]
        assert body["stream"] is False
        assert "task" in body["prompt"]

    def test_custom_url_and_model_overrides(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"passed": true}')
            verify_photo_with_llm(
                "b64",
                "task",
                ollama_url="http://gpu-host:11434",
                vision_model="custom-vision:1.0",
            )
        req = urlopen.call_args.args[0]
        assert req.full_url == "http://gpu-host:11434/api/generate"
        body = json.loads(req.data.decode())
        assert body["model"] == "custom-vision:1.0"


# ── generate_task_with_llm ──


class TestGenerateTaskCategories:
    def test_chore_category(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"task": "Wash dishes", "hint": "Clean sink"}')
            result = generate_task_with_llm("chore")
        assert result == {"ok": True, "task": "Wash dishes", "hint": "Clean sink"}
        body = json.loads(urlopen.call_args.args[0].data.decode())
        assert "chore" in body["prompt"].lower()

    def test_exercise_category(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"task": "30 squats", "hint": "Mid-squat"}')
            result = generate_task_with_llm("exercise")
        assert result["task"] == "30 squats"
        body = json.loads(urlopen.call_args.args[0].data.decode())
        assert "exercise" in body["prompt"].lower()

    def test_creative_category(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"task": "Draw a cat", "hint": "Drawing"}')
            result = generate_task_with_llm("creative")
        assert result["task"] == "Draw a cat"

    def test_general_category(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"task": "Tidy desk", "hint": "Clean desk"}')
            result = generate_task_with_llm("general")
        assert result["task"] == "Tidy desk"

    def test_service_category(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"task": "Make tea", "hint": "Tea cup"}')
            result = generate_task_with_llm("service")
        assert result["task"] == "Make tea"

    def test_unknown_category_falls_back_to_general(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"task": "fallback", "hint": ""}')
            generate_task_with_llm("nonexistent-category")
        body = json.loads(urlopen.call_args.args[0].data.decode())
        # "general" prompt mentions "30 minutes"
        assert "30 minutes" in body["prompt"]


class TestGenerateTaskParsing:
    def test_json_extracted_from_surrounding_prose(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response(
                'Sure thing!\n{"task": "Sweep floor", "hint": "Clean floor"}\nDone.'
            )
            result = generate_task_with_llm("chore")
        assert result == {"ok": True, "task": "Sweep floor", "hint": "Clean floor"}

    def test_missing_task_field_defaults_empty(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"hint": "only hint"}')
            result = generate_task_with_llm()
        assert result == {"ok": True, "task": "", "hint": "only hint"}

    def test_missing_hint_field_defaults_empty(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"task": "task only"}')
            result = generate_task_with_llm()
        assert result == {"ok": True, "task": "task only", "hint": ""}

    def test_no_json_returns_truncated_response_as_task(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response("plain text response no braces")
            result = generate_task_with_llm()
        assert result == {"ok": True, "task": "plain text response no braces", "hint": ""}

    def test_response_text_truncated_to_200_chars(self):
        long_response = "task " * 100
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response(long_response)
            result = generate_task_with_llm()
        assert len(result["task"]) == 200

    def test_malformed_json_inside_braces_falls_back(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response("{not valid json}")
            result = generate_task_with_llm()
        # JSONDecodeError swallowed, falls through to truncated response
        assert result["ok"] is True
        assert result["task"] == "{not valid json}"


class TestGenerateTaskErrors:
    def test_http_error_returns_safe_fallback(self):
        with patch("focuslock_llm.urllib.request.urlopen", side_effect=OSError("dead")):
            result = generate_task_with_llm()
        assert result == {
            "ok": False,
            "task": "Clean the kitchen",
            "hint": "Photo of a clean kitchen",
        }

    def test_response_decode_error_returns_safe_fallback(self):
        bad_resp = MagicMock()
        bad_resp.read.return_value = b"not json at all"
        with patch("focuslock_llm.urllib.request.urlopen", return_value=bad_resp):
            result = generate_task_with_llm()
        assert result["ok"] is False
        assert result["task"] == "Clean the kitchen"


class TestGenerateTaskPayload:
    def test_default_url_and_model_used(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"task": "x", "hint": ""}')
            generate_task_with_llm()
        req = urlopen.call_args.args[0]
        assert req.full_url == "http://localhost:11434/api/generate"
        body = json.loads(req.data.decode())
        assert body["model"] == DEFAULT_TEXT_MODEL
        assert body["stream"] is False

    def test_custom_url_and_model_overrides(self):
        with patch("focuslock_llm.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _ollama_response('{"task": "x", "hint": ""}')
            generate_task_with_llm(
                "chore",
                ollama_url="http://gpu:11434",
                text_model="custom-text:7b",
            )
        req = urlopen.call_args.args[0]
        assert req.full_url == "http://gpu:11434/api/generate"
        body = json.loads(req.data.decode())
        assert body["model"] == "custom-text:7b"
