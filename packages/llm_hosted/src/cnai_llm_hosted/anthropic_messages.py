from ._base import HostedBase


class AnthropicMessagesBackend(HostedBase):
    path = "/v1/messages"

    def _headers(self):
        return {"x-api-key": self.secret.reveal(), "anthropic-version": str(self.p.get("api_version", "2023-06-01"))}

    def _body(self, system, user, p):
        return {"model": self.info.model_id, "system": system, "messages": [{"role": "user", "content": user}],
                "temperature": p.get("temperature", 0), "max_tokens": int(p.get("max_output_tokens", 1500))}

    def _extract(self, d):
        u = d.get("usage") or {}
        text = "".join(b.get("text", "") for b in d["content"] if b.get("type") == "text")
        return text, u.get("input_tokens"), u.get("output_tokens"), d.get("id")
