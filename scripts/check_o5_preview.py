"""Check the isolated O5 container using gateway headers and synthetic credentials."""

import os

import httpx


def main() -> None:
    origin = os.environ["PAIYA_O5_ORIGIN"]
    proxy = {
        "Host": os.environ["PAIYA_O5_BACKEND_HOST"],
        "X-Paiya-Proxy-Key": os.environ["PAIYA_O5_PROXY_KEY"],
    }
    pairing_code = os.environ["PAIYA_O5_PAIRING_CODE"]
    passphrase = os.environ["PAIYA_O5_TEST_PASSPHRASE"]

    with httpx.Client(base_url="http://127.0.0.1:8785", timeout=10) as api:
        def unlock(pair: str | None = None) -> httpx.Response:
            challenge = api.get("/v1/auth/challenge", headers=proxy)
            assert challenge.status_code == 200
            return api.post(
                "/v1/auth/unlock",
                headers={**proxy, "Origin": origin, "X-CSRF-Token": challenge.json()["nonce"]},
                json={"passphrase": passphrase, "pairing_code": pair},
            )

        assert api.get("/v1/auth/challenge").status_code == 403
        assert api.get("/v1/posts", headers=proxy).status_code == 401
        unaffiliated = unlock()
        assert unaffiliated.status_code == 403
        assert unaffiliated.json()["detail"]["code"] == "pairing_required"
        assert not unaffiliated.headers.get_list("set-cookie")

        signed_in = unlock(pairing_code)
        assert signed_in.status_code == 200
        assert signed_in.headers["cache-control"] == "no-store"
        assert set(signed_in.cookies.keys()) == {"paiya_owner_session", "__Host-paiya_o5_device"}
        for cookie in signed_in.headers.get_list("set-cookie"):
            assert all(flag in cookie.lower() for flag in ("secure", "httponly", "samesite=strict"))
            assert "domain=" not in cookie.lower()

        # TLS terminates at the gateway. Send the values from the browser's HTTPS cookie jar.
        session = signed_in.cookies["paiya_owner_session"]
        device = signed_in.cookies["__Host-paiya_o5_device"]
        read = {**proxy, "Cookie": f"paiya_owner_session={session}; __Host-paiya_o5_device={device}"}
        groups = api.get("/v1/groups", headers=read)
        assert groups.status_code == 200
        assert [group["group_key"] for group in groups.json()["groups"]] == ["synthetic-demo"]
        assert groups.headers["cache-control"] == "no-store"
        assert api.get("/v1/posts", headers={**proxy, "Cookie": f"paiya_owner_session={session}"}).status_code == 401
        assert api.get("/v1/admin/health", headers=read).status_code == 200

        write = {**read, "Origin": origin}
        assert api.post("/v1/auth/logout", headers=write).status_code == 403
        csrf = signed_in.json()["csrf_token"]
        assert api.post(
            "/v1/auth/logout", headers={**write, "Origin": "https://other.test", "X-CSRF-Token": csrf}
        ).status_code == 403
        assert api.post("/v1/auth/logout", headers={**write, "X-CSRF-Token": csrf}).status_code == 200
        assert api.get("/v1/posts", headers=read).status_code == 401

    print("O5 container verified gateway, device pairing, secure cookies, synthetic dashboard and logout.")


if __name__ == "__main__":
    main()
