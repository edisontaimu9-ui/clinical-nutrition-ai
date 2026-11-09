from ._base import HostedBase


class OpenAICompatBackend(HostedBase):
    path = "/chat/completions"

    def _headers(self):
        return {"Authorization": "Bearer " + self.secret.reveal()}

    def _body(self, system, user, p):
        body = {"model": self.info.model_id, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": p.get("temperature", 0), "max_tokens": int(p.get("max_output_tokens", 1500))}
        if "seed" in p:
            body["seed"] = p["seed"]
        if p.get("json_mode"):
            body["response_format"] = {"type": "json_object"}
        return body

    def _extract(self, d):
        u = d.get("usage") or {}
        return d["choices"][0]["message"]["content"], u.get("prompt_tokens"), u.get("completion_tokens"), d.get("id")
