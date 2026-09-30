import httpx

DEFAULT_JEV_ENDPOINT = "http://127.0.0.1:8791/v1/systemone"


class OpenJevClient:
    def __init__(self, endpoint: str = DEFAULT_JEV_ENDPOINT):
        self._endpoint = endpoint

    def ask(self, state: str, questions: dict) -> dict:
        try:
            response = httpx.post(
                self._endpoint,
                headers={"Content-Type": "application/json"},
                json={"state": state, "questions": questions},
                timeout=httpx.Timeout(180.0, connect=5.0),
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            # Try to get a more specific error from the response body
            try:
                error_body = e.response.json()
                error_message = error_body.get("error", "Unknown Jev server error")
                raise ConnectionError(f"Jev server returned an error: {error_message}") from e
            except Exception:
                raise ConnectionError("Jev server returned a non-JSON error response") from e
        except httpx.RequestError as e:
            raise ConnectionError(f"Could not connect to Jev server at {self._endpoint}") from e
