import httpx

# Direct TypeSafe endpoint (the old Vercel AI Gateway route is retired).
# See docs/typesafe_jev_api.md for the full API reference.
JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"


class JevClient:
    def __init__(self, api_key: str, endpoint: str = JEV_ENDPOINT):
        self._endpoint = endpoint
        self._api_key = api_key

    def ask(self, state: str, questions: dict) -> dict:
        try:
            response = httpx.post(
                self._endpoint,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                json={"state": state, "questions": questions, "model": JEV_MODEL},
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            try:
                error_body = e.response.json()
                error_message = error_body.get("error", "Unknown Jev API error")
                raise ConnectionError(f"Jev API returned an error: {error_message}") from e
            except Exception:
                raise ConnectionError(
                    f"Jev API returned a non-JSON error response: {e.response.text}"
                ) from e
        except httpx.RequestError as e:
            raise ConnectionError(f"Could not connect to Jev API at {self._endpoint}") from e