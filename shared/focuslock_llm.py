# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock LLM Integration — Ollama-based photo verification and task generation.

Uses minicpm-v (vision) for photo task pass/fail verification and
dolphin-llama3:8b (text) for creative task generation.
"""

import json
import logging
import urllib.request
from datetime import datetime

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_VISION_MODEL = "minicpm-v"
DEFAULT_TEXT_MODEL = "dolphin-llama3:8b"


def verify_photo_with_llm(
    photo_b64, task_text, *, ollama_url=DEFAULT_OLLAMA_URL, vision_model=DEFAULT_VISION_MODEL, on_evidence=None
):
    """Verify a photo task submission via Ollama vision model.

    Args:
        photo_b64: Base64-encoded photo string.
        task_text: The task description the photo should prove.
        ollama_url: Ollama API base URL.
        vision_model: Vision model name (e.g. "minicpm-v").
        on_evidence: Optional callback(text, evidence_type) to send evidence.
            Called with verdict details on both pass and fail.

    Returns:
        Dict with keys: ok (bool), passed (bool), reason (str).
    """
    if not photo_b64:
        return {"ok": False, "passed": False, "reason": "No photo provided"}

    try:
        prompt = (
            f"You are a task verification assistant. The user was asked to "
            f"complete this task:\n\n"
            f'"{task_text}"\n\n'
            f"They have submitted a photo as proof. Evaluate whether the photo "
            f"shows the task has been completed. Be reasonable but not "
            f"gullible.\n\n"
            f"Respond with ONLY a JSON object: "
            f'{{"passed": true/false, "reason": "brief explanation"}}'
        )
        payload = {
            "model": vision_model,
            "prompt": prompt,
            "images": [photo_b64],
            "stream": False,
        }
        req = urllib.request.Request(
            f"{ollama_url}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
        response_text = resp.get("response", "")

        # Try to parse JSON from response
        try:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response_text[start:end])
                passed = result.get("passed", False)
                reason = result.get("reason", "No reason given")

                # Send evidence email either way
                if on_evidence:
                    verdict = "PASSED" if passed else "FAILED"
                    on_evidence(
                        f"Photo Task Verification: {verdict}\n\n"
                        f"Task: {task_text}\n"
                        f"Result: {reason}\n"
                        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        f"photo task {verdict.lower()}",
                    )

                return {"ok": True, "passed": passed, "reason": reason}
        except json.JSONDecodeError:
            pass

        # Fallback: check for obvious pass/fail keywords
        lower = response_text.lower()
        passed = any(kw in lower for kw in ("pass", "true", "completed", "yes"))
        return {"ok": True, "passed": passed, "reason": response_text[:200]}

    except Exception as e:
        print(f"[llm] Verification error: {e}")
        return {"ok": False, "passed": False, "reason": str(e)}


def generate_task_with_llm(category="general", *, ollama_url=DEFAULT_OLLAMA_URL, text_model=DEFAULT_TEXT_MODEL):
    """Generate a creative task via Ollama text model.

    Args:
        category: One of "chore", "exercise", "creative", "service",
            or "general" (default).
        ollama_url: Ollama API base URL.
        text_model: Text model name (e.g. "dolphin-llama3:8b").

    Returns:
        Dict with keys: ok (bool), task (str), hint (str).
    """
    try:
        prompts = {
            "chore": (
                "Generate a specific household chore task. Examples: "
                "'Clean the kitchen counter', 'Vacuum the living room', "
                "'Organize the bathroom cabinet'. Be specific and "
                "verifiable by photo."
            ),
            "exercise": (
                "Generate a specific exercise task. Examples: "
                "'Do 20 pushups', 'Hold a plank for 60 seconds', "
                "'Do 15 squats'. Should be verifiable by a photo of "
                "the person in the act."
            ),
            "creative": (
                "Generate a creative task. Examples: "
                "'Draw a picture of a lion', "
                "'Write a haiku about obedience', "
                "'Build something with household items'. Should produce "
                "a visible result for photo verification."
            ),
            "general": (
                "Generate a task for someone to complete. It should be "
                "specific, completable within 30 minutes, and verifiable "
                "by a photo. Can be a chore, exercise, creative task, "
                "or act of service."
            ),
            "service": (
                "Generate an act-of-service task for someone to do for "
                "their partner. Examples: 'Make a cup of tea', "
                "'Prepare a snack plate', "
                "'Write a love note and place it on the pillow'."
            ),
        }
        prompt = prompts.get(category, prompts["general"])
        prompt += (
            "\n\nRespond with ONLY a JSON object: "
            '{"task": "the task description", '
            '"hint": "what the photo should show"}'
        )

        payload = {
            "model": text_model,
            "prompt": prompt,
            "stream": False,
        }
        req = urllib.request.Request(
            f"{ollama_url}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
        response_text = resp.get("response", "")

        try:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response_text[start:end])
                return {
                    "ok": True,
                    "task": result.get("task", ""),
                    "hint": result.get("hint", ""),
                }
        except json.JSONDecodeError:
            pass

        return {"ok": True, "task": response_text[:200], "hint": ""}

    except Exception as e:
        print(f"[llm] Generation error: {e}")
        return {
            "ok": False,
            "task": "Clean the kitchen",
            "hint": "Photo of a clean kitchen",
        }
