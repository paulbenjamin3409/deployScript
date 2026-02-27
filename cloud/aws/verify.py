"""Post-deployment infrastructure verification for AWS services.

Checks both the control plane (Lambda function state via AWS CLI) and the
data plane (end-to-end HTTP requests through API Gateway) to confirm the
deployment is healthy before returning control to the caller.

Checks performed
----------------
1. lambda.function.state       – Lambda is Active with LastUpdateStatus=Successful
2. api_gateway.health          – GET /health through API Gateway returns 200
3. api_gateway.cors_preflight  – OPTIONS /health returns CORS headers
4. api_gateway.error_handling  – Unknown route returns 4xx, not 502

Checks 2-4 are skipped (with a warning) when ``api_gateway_url`` is not set
in the deployment config.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from cloud.aws.cli import AwsCli
from cloud.core.console import error, info, success, warn
from cloud.core.models import DeploymentConfig


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str


class AwsInfraVerifier:
    """Run post-deployment checks against Lambda and API Gateway."""

    def __init__(self, config: DeploymentConfig, cli: AwsCli) -> None:
        self._config = config
        self._cli = cli

    def run_all(self) -> list[CheckResult]:
        """Execute every check, print a pass/fail line for each, and return results."""
        info("\nPost-deployment infrastructure verification:")

        results: list[CheckResult] = []

        # Control-plane: Lambda function state (always runs)
        results.append(self._check_lambda_state())

        # Data-plane: end-to-end HTTP through API Gateway
        api_url = (self._config.api_gateway_url or "").rstrip("/")
        if api_url:
            results.append(self._check_api_gateway_health(api_url))
            results.append(self._check_api_gateway_cors(api_url))
            results.append(self._check_api_gateway_bad_route(api_url))
        else:
            warn(
                "api_gateway_url is not set; skipping API Gateway HTTP checks.\n"
                "  Add it to config/local.yaml:\n"
                "  api_gateway_url: https://<id>.execute-api.<region>.amazonaws.com/"
            )

        for result in results:
            _print_check(result)

        return results

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_lambda_state(self) -> CheckResult:
        """Verify the Lambda function is Active and its last update Successful."""
        name = "lambda.function.state"
        func = self._config.lambda_function_name
        if not func:
            return CheckResult(name, False, "lambda_function_name is not configured")

        try:
            data = self._cli.json(
                [
                    "lambda", "get-function-configuration",
                    "--function-name", func,
                    "--region", self._config.aws_region,
                ]
            )
        except Exception as exc:
            return CheckResult(name, False, f"Could not query Lambda configuration: {exc}")

        state = data.get("State", "Unknown")
        last_status = data.get("LastUpdateStatus", "Unknown")

        if state == "Active" and last_status == "Successful":
            return CheckResult(
                name, True,
                f"'{func}' is Active (LastUpdateStatus=Successful)"
            )
        return CheckResult(
            name, False,
            f"'{func}' is not healthy — State={state}, LastUpdateStatus={last_status}. "
            "Wait for any in-progress update to complete and re-run."
        )

    def _check_api_gateway_health(self, api_url: str) -> CheckResult:
        """Call GET /health through API Gateway and expect HTTP 200."""
        name = "api_gateway.health"
        url = f"{api_url}/health"
        status, body = _http_get(url, timeout=15)

        if status == 200:
            try:
                summary = json.loads(body)
            except Exception:
                summary = body[:120] if body else "(empty body)"
            return CheckResult(name, True, f"GET /health → 200 OK  {summary}")

        return CheckResult(
            name, False,
            f"GET /health → {status or 'unreachable'} (expected 200). "
            + (f"Body: {body[:200]}" if body else "No response body received."),
        )

    def _check_api_gateway_cors(self, api_url: str) -> CheckResult:
        """Send an OPTIONS preflight to /health and verify CORS headers are present."""
        name = "api_gateway.cors_preflight"
        url = f"{api_url}/health"
        origin = (self._config.cors_origin or "https://example.com").rstrip("/")

        status, headers = _http_options(url, origin=origin, timeout=15)
        acao = headers.get("access-control-allow-origin", "")

        if status in (200, 204) and acao:
            return CheckResult(
                name, True,
                f"OPTIONS /health → {status}; Access-Control-Allow-Origin: {acao}"
            )
        if status in (200, 204) and not acao:
            return CheckResult(
                name, False,
                f"OPTIONS /health → {status} but Access-Control-Allow-Origin header is absent. "
                "Check the CORS_ORIGIN environment variable on the Lambda function and "
                "that the Lambda response includes the header.",
            )
        return CheckResult(
            name, False,
            f"OPTIONS /health → {status or 'unreachable'} (expected 200 or 204).",
        )

    def _check_api_gateway_bad_route(self, api_url: str) -> CheckResult:
        """GET a non-existent path; 502 means Lambda is crash-looping or misconfigured."""
        name = "api_gateway.error_handling"
        url = f"{api_url}/__infra_verify_nonexistent__"
        status, _ = _http_get(url, timeout=15)

        if status == 0:
            return CheckResult(name, False, "Could not reach API Gateway (connection failed).")

        if status == 502:
            return CheckResult(
                name, False,
                "Unknown route returned 502 Bad Gateway — Lambda may be crash-looping or "
                "the API Gateway integration is broken. Check CloudWatch Logs for errors.",
            )

        # 404, 403, or a catch-all 200 are all acceptable
        return CheckResult(
            name, True,
            f"Unknown route → {status} (not 502; API Gateway → Lambda integration is healthy).",
        )


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only, no third-party dependencies)
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int) -> tuple[int, str]:
    """GET *url* and return ``(status_code, body)``.  Returns ``(0, error)`` on failure."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, body
    except Exception as exc:
        return 0, str(exc)


def _http_options(url: str, origin: str, timeout: int) -> tuple[int, dict]:
    """Send an OPTIONS preflight to *url* and return ``(status_code, headers_dict)``.

    Uses ``http.client`` directly so we get full header access without urllib's
    redirect/error-raising behaviour interfering.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        conn = http.client.HTTPSConnection(host, timeout=timeout)
        try:
            conn.request(
                "OPTIONS",
                path,
                headers={
                    "Host": host,
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}
        finally:
            conn.close()
    except Exception:
        return 0, {}


def _print_check(result: CheckResult) -> None:
    label = "  [PASS]" if result.ok else "  [FAIL]"
    msg = f"{label} {result.name}: {result.message}"
    if result.ok:
        success(msg)
    else:
        error(msg)
