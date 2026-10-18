#!/usr/bin/env python
"""Audit the DEPLOYED Pages surfaces for CSP violations (#3525).

WHY THIS SCRIPT EXISTS
----------------------
Cloudflare's edge injects a Web Analytics beacon
(``https://static.cloudflareinsights.com/beacon.min.js/<version>``) into HTML
responses **whose request carries ``Accept: text/html``** — not a User-Agent
test. Neither a plain ``curl`` nor a local ``wrangler pages dev`` preview ever
sees the tag, so no local check can tell whether a policy admits it. The first
CSP shipped for #3525 omitted the beacon origin, blocked it in production, and
was caught only by the post-merge ``verify-legal`` job — 8 console-error
failures. This script is the direct, pre-merge/at-any-time detector for that
class: it drives the real surfaces in a real browser and reports every CSP
violation, page error and failed request.

QUICK HEADER-LEVEL PROBE (no browser needed)::

    curl -sH 'Accept: text/html' https://premiselabs.co/ | grep -c cloudflareinsights

USAGE (read-only GETs; refuses https unless ALLOW_PROD=1, matching the sibling
suites in this directory)::

    ALLOW_PROD=1 .venv/bin/python tests/e2e/audit_prod_csp.py
    ALLOW_PROD=1 .venv/bin/python tests/e2e/audit_prod_csp.py https://app.premiselabs.co/

Exits 0 when no surface reported a CSP violation AND no surface broke the
beacon contract (see BEACON HONESTY above), 1 otherwise, 2 when an https target
is probed without `ALLOW_PROD=1`. Non-CSP console/page errors and failed
requests are printed separately (they never count toward the violation total)
so a completeness claim about "no OTHER failed request" is readable off the
output instead of asserted from a blind instrument.

NOTE: `/admin` (the `ADMIN_CSP` surface) requires an authenticated session, so it
is not in the default URL set; it is covered statically by the `securityHeaders`
guard, which pins its policy.

BEACON HONESTY (#4706/#8334, the #4762 addendum)
------------------------------------------------
A surface with **no** CSP violation can still be broken in a way no policy text
shows. #8334 removed `https://cloudflareinsights.com` from `connect-src` on the
measured basis that under AUTOMATIC injection the beacon's RUM payload goes to
the page's OWN origin (`POST /cdn-cgi/rum`); the third-party host is the
MANUAL-embed default, which the beacon only uses when `data-cf-beacon.version`
is absent. That same-origin property is a property of the **injected tag**, not
of our source, so the policy guard cannot see it. Two ways it silently breaks:

* the zone is switched to "Enable with JS Snippet installation" (or the edge
  stops emitting `version`) → the beacon POSTs cross-origin and
  `connect-src 'self'` refuses it: analytics lost plus a console error; or
* the beacon LOADS and reports nowhere — a failure with no console message at
  all, and therefore invisible to the violation scan above.

So each surface also records its request URLs and its injected-tag presence, and
this script fails on (a) any request to the bare `cloudflareinsights.com` apex
host, or (b) a tag that was present and was not refused by any OBSERVED CSP
violation, with no same-origin `/cdn-cgi/rum` POST observed. `no-tag` (the edge
injecting nothing at all) is reported, never failed: Web Analytics is a zone
setting and #4706's keep-or-drop decision is open. A surface whose navigation
FAILED is `not-measured`, never `no-tag`: the run never saw that document, so it
must not contribute to the "carried NO injected beacon tag" count.

The host match is EXACT. A substring test also matches
`static.cloudflareinsights.com` — the beacon's own SCRIPT origin, which every
policy must allow — and would red a healthy surface.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

# The edge-injected beacon's SCRIPT origin host (``script-src``) …
BEACON_SCRIPT_HOST = "static.cloudflareinsights.com"
# … and the same-origin RUM path the beacon POSTs to under automatic injection.
BEACON_RUM_PATH = "/cdn-cgi/rum"
# The MANUAL-embed RUM endpoint's host. Every policy omits it since #8334, so a
# request to it means the tag fell back to the cross-origin default.
MANUAL_RUM_HOST = "cloudflareinsights.com"

DEFAULT_URLS = [
    "https://premiselabs.co/",
    "https://premiselabs.co/docs/",
    "https://tortoise.premiselabs.co/",
    # A Function-rendered HTML surface (`blog/[[path]].ts` → `_lib.ts`), where
    # `_headers` does NOT apply and the policy is the marketing `RELAXED_CSP`.
    "https://tortoise.premiselabs.co/blog",
    "https://app.premiselabs.co/",
    "https://app.premiselabs.co/signup",
    "https://app.premiselabs.co/auth",
    "https://app.premiselabs.co/team",
]


def _violations(console: list[str], failed: list[str]) -> list[str]:
    """CSP refusals, from either the console or a failed request.

    Matched on the CSP phrases, NOT a bare ``Refused to`` — Chrome's strict-MIME
    refusal (``Refused to apply style … MIME type``) is a different class and
    must not read as a CSP violation.
    """
    hits = [m for m in console if "Content Security Policy" in m or "violates the following" in m]
    return hits + [f"{f} (request failed)" for f in failed if "csp" in f.lower()]


def _directives(csp: str) -> dict[str, list[str]]:
    """Parse a CSP into ``{directive: [source, …]}``.

    Deliberately minimal: this script only needs to answer "does the policy
    admit the beacon's script origin", and a half-parser that silently returns
    nothing would answer "no" for every policy — the direction that INVENTS a
    finding. An unparseable directive is therefore kept as a bare token list,
    which can only fail to match the host (the `tag-blocked` arm), never make
    one up.
    """
    out: dict[str, list[str]] = {}
    for part in csp.split(";"):
        tokens = part.split()
        if tokens:
            out.setdefault(tokens[0].lower(), tokens[1:])
    return out


def _source_admits_beacon(src: str) -> bool:
    """Does ONE CSP source expression admit ``static.cloudflareinsights.com``?

    The host-source grammar is much wider than the exact spelling the shipped
    policies happen to use, and every form below really does let the beacon load:
    the scheme source ``https:``, the bare ``*``, a subdomain wildcard
    (``https://*.cloudflareinsights.com`` or ``*.cloudflareinsights.com``), a
    scheme-relative host (``//static.cloudflareinsights.com``), a schemeless host
    (``static.cloudflareinsights.com``), an explicit port, and a path-scoped
    source (``https://static.cloudflareinsights.com/beacon.min.js``).

    Enumerating spellings is the FAIL-OPEN direction for the arm that consumes
    this: "not admitted" suppressed the `rum-missing` report, and a beacon that
    really did load leaves no CSP violation to fail the surface — so an admit
    form the matcher did not recognise printed a benign `tag-blocked` on a green
    run. A source that NAMES the beacon's zone therefore admits it however it is
    written; only a source that does not mention the zone (``'self'``, another
    host, ``'none'``, an http-only scheme) refuses.
    """
    token = src.strip().lower()
    if not token:
        return False
    # `*` (any host, any network scheme) and the `https:` scheme source admit
    # every https URL, the beacon's included. `http:` deliberately does NOT: the
    # beacon is https-only, so an http-only source cannot load it.
    if token in ("*", "https:", "https://*"):
        return True
    host_part = token
    for prefix in ("https://", "http://", "wss://", "//"):
        if host_part.startswith(prefix):
            host_part = host_part[len(prefix):]
            break
    # Host-source only: a scheme (`data:`, `blob:`) or a bare keyword (`'self'`,
    # `'none'`, a nonce/hash) has no host to compare. A path part and an explicit
    # port are both irrelevant to WHICH host is admitted.
    host = host_part.split("/", 1)[0]
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    if host == "*":
        return True
    # `*.cloudflareinsights.com` → `.cloudflareinsights.com`. CSP's `*.` matches
    # subdomains but not the apex, and the beacon's host IS a subdomain, so the
    # label-boundary suffix test is the right predicate (and `notexample.com`
    # must not match `example.com`).
    if host.startswith("*."):
        host = host[1:]
    return host == MANUAL_RUM_HOST or host.endswith("." + MANUAL_RUM_HOST)


def _admits_beacon_script(csp: str) -> bool:
    """Does this policy let the edge-injected beacon SCRIPT load?

    Directive precedence is real CSP semantics, not a preference: an absent
    ``script-src-elem`` falls back to ``script-src``, which falls back to
    ``default-src``. Checking all three as a union would answer `True` for a
    policy whose `script-src-elem` explicitly EXCLUDES the host, which is the
    case the beacon report exists to catch.

    A `False` here is only ever a HINT — ``_beacon_verdict`` requires an OBSERVED
    refusal before it treats the beacon as blocked, because the text parse is a
    proxy whose miss direction (suppressing `rum-missing`) reads green.
    """
    directives = _directives(csp)
    sources: list[str] | None = None
    for name in ("script-src-elem", "script-src", "default-src"):
        if name in directives:
            sources = directives[name]
            break
    if sources is None:
        return False
    return any(_source_admits_beacon(src) for src in sources)


def _beacon_verdict(
    requests: list[tuple[str, str]],
    csp: str,
    tag_present: bool,
    page_url: str,
    violations: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Classify the beacon contract for ONE surface.

    ``requests`` is ``[(method, url), …]`` as observed by the page (including
    the teardown half — the RUM POST goes out on `load`/`visibilitychange`).
    Returns ``(label, findings)``; a non-empty ``findings`` is a failure. Pure,
    so the contract is pinned against synthetic request lists instead of
    against production.

    ``page_url`` supplies the origin the RUM payload must stay ON: "same-origin"
    means the PAGE's origin, because `connect-src 'self'` admits exactly that —
    accepting any `/cdn-cgi/rum` POST would report `rum-ok` for a payload the
    browser refused to deliver. It must be the LANDED url, not the requested
    one: `https://premiselabs.co/docs/` 301s to `https://tortoise.premiselabs.co/docs`,
    so comparing against the requested host reports `rum-missing` on a healthy
    surface (measured: the only false positive this arm produced).

    ``violations`` is the surface's violation scan (what the CONSOLE actually
    reported). It gates ``tag-blocked``: the policy text alone is a proxy, and a
    proxy that answers "blocked" for a beacon that in fact LOADED suppresses
    ``rum-missing`` — the one arm that sees analytics lost with no console error.

    Labels: ``no-tag`` (edge injected nothing — reported, not failed),
    ``tag-blocked`` (an OBSERVED refusal of the beacon script; the policy scan
    owns it), ``rum-missing``, ``rum-ok``. ``main`` uses one further label of its
    own — ``not-measured``, for a surface whose navigation failed — so an
    unvisited surface is never counted as an observed absence of the tag. A
    cross-origin RUM request is not a label of its own: the label reports the
    same-origin contract, and the cross-origin request arrives as a
    ``beacon-cross-origin`` FINDING alongside it (so a surface that reports
    same-origin AND leaks one to the manual endpoint is still a failure).
    """
    findings: list[str] = []
    rum_same_origin = False
    page_host = (urlsplit(page_url).hostname or "").lower()
    for method, url in requests:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        # HOST-EXACT: `static.cloudflareinsights.com` is the script origin we
        # REQUIRE, and an endswith/`in` test would match it here and flip every
        # healthy surface to this finding.
        if host == MANUAL_RUM_HOST:
            findings.append(
                f"[beacon-cross-origin] {method} {url} — the beacon used the "
                "MANUAL-embed RUM endpoint, which no policy admits since #8334"
            )
        elif (
            method.upper() == "POST"
            # PATH-EXACT, not a suffix test: the RUM endpoint is `/cdn-cgi/rum` on
            # the zone root, so `/x/cdn-cgi/rum` is a DIFFERENT endpoint and a
            # `endswith` would count it as the same-origin one.
            and parts.path.rstrip("/") == BEACON_RUM_PATH
            and host == page_host
        ):
            rum_same_origin = True

    if not tag_present:
        # Absence of the tag is a ZONE SETTING (#4706 keep-or-drop is open), not
        # a defect in this repo's policy. Reported so a silent switch-off is
        # visible; never a failure.
        return "no-tag", findings

    # BLOCKED IS AN OBSERVATION, NOT A PARSE. Requiring the recorded violation
    # keeps the policy text as a corroborating hint: an admit form this parser
    # does not enumerate must not be able to silence `rum-missing` (the beacon
    # loaded, so no violation exists to fail the surface). When the refusal WAS
    # observed, the violation scan already fails the surface and a second finding
    # here would double-count one cause.
    refused = any(BEACON_SCRIPT_HOST in v.lower() for v in (violations or ()))
    if refused and not _admits_beacon_script(csp):
        return "tag-blocked", findings

    if rum_same_origin:
        return "rum-ok", findings

    findings.append(
        "[rum-missing] the beacon LOADED on a surface whose policy admits it, "
        f"but no same-origin POST {BEACON_RUM_PATH} was observed — the RUM "
        "payload reported nowhere (analytics lost with no console error)"
    )
    return "rum-missing", findings


def _count_labels(labels: list[str]) -> tuple[int, int]:
    """``(no_tag, not_measured)`` from the per-surface verdict labels.

    A surface whose navigation failed is ``not-measured``, NOT ``no-tag``: the run
    never saw that document, so it must not contribute to the summary's
    "carried NO injected beacon tag" count. That line is the signal for a CHANGED
    injection format, so it has to stay a measurement — a 5xx or a timeout
    inflating it sends a triager after a zone setting.
    """
    return labels.count("no-tag"), labels.count("not-measured")


def _other_errors(console: list[str], failed: list[str]) -> list[str]:
    """Non-CSP console/page errors and failed requests.

    Kept OUT of `_violations`: a network blip or an unrelated app error must not
    read as a CSP violation, and a completeness claim ("the beacon was the only
    failure") is only checkable if this class is visible rather than discarded.
    """
    csp = set(_violations(console, failed))
    return [m for m in console if m not in csp] + [f for f in failed if "csp" not in f.lower()]


def main(argv: list[str]) -> int:
    urls = argv[1:] or DEFAULT_URLS
    # `.lower()`: a scheme is case-insensitive (RFC 3986), and Chromium navigates
    # `HTTPS://…`, so a case-sensitive test would let an un-opted-in run reach
    # production through an upper-case URL.
    if any(u.lower().startswith("https://") for u in urls) and os.environ.get("ALLOW_PROD") != "1":
        print("refusing https targets without ALLOW_PROD=1 (read-only, but be explicit)")
        return 2

    dirty = 0
    beacon_dirty = 0
    labels: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for url in urls:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            console: list[str] = []
            failed: list[str] = []
            requests: list[tuple[str, str]] = []
            # Default-arg binding: B023 — the handlers must capture THIS loop
            # iteration's lists, not the loop variable.
            page.on("console", lambda m, c=console: c.append(f"[{m.type}] {m.text}"))
            page.on("pageerror", lambda e, c=console: c.append(f"[pageerror] {e}"))
            page.on("requestfailed", lambda r, f=failed: f.append(f"{r.url} :: {r.failure}"))
            # The beacon contract's evidence: every request the page makes, and
            # whether the edge injected the tag at all.
            page.on("request", lambda r, q=requests: q.append((r.method, r.url)))
            resp = None
            csp = ""
            kind = "?"
            tag_present = False
            # The origin `connect-src 'self'` actually binds is the LANDED one:
            # `premiselabs.co/docs/` 301s to `tortoise.premiselabs.co/docs`.
            landed_url = url
            goto_error: Exception | None = None
            try:
                resp = page.goto(url, wait_until="load", timeout=45_000)
                landed_url = page.url or url
                csp = (resp.headers.get("content-security-policy") or "") if resp is not None else ""
                # `connect-src 'self'` with no third-party host is the strict
                # policy; anything with posthog is the relaxed one.
                kind = "STRICT" if "connect-src 'self'" in csp and "posthog" not in csp else (
                    "RELAXED" if csp else "NONE"
                )
                # Read the INJECTED DOCUMENT, not our source: the edge adds the
                # tag at response time, so this is the only way to tell "the zone
                # stopped injecting" from "the policy stopped admitting it" —
                # and a CSP-blocked script element is still in the DOM, so the
                # check survives the blocked case too.
                tag_present = bool(
                    page.evaluate(
                        "(host) => Array.from(document.querySelectorAll('script[src]'))"
                        ".some((s) => s.src.includes(host))",
                        BEACON_SCRIPT_HOST,
                    )
                )
                # A bounded settle for post-`load` refusals — but the READ must
                # not happen here. The beacon sends its RUM payload on
                # `load`/`visibilitychange`, and the `visibilitychange` half fires
                # at page teardown; a snapshot taken before the page closes drops
                # every refusal emitted after the settle and prints OK on it. So:
                # settle, close the PAGE, THEN read (see `page.close()` below).
                page.wait_for_timeout(3_500)
            except Exception as exc:
                goto_error = exc
            # End the PAGE first — `page.close()`, NOT `ctx.close()`. The
            # beacon's `navigator.sendBeacon` RUM POST goes out on
            # `load`/`visibilitychange`, and only `page.close()` fires
            # `visibilitychange`/`pagehide`/`unload`; `ctx.close()` teardown fires
            # none of them (measured: a `connect-src` refusal emitted on
            # `visibilitychange` appears in the drained lists after `page.close()`
            # and NOT after `ctx.close()`). So the verdict must be read after the
            # PAGE ends — reading after only the context is closed still drops the
            # teardown class this script exists to detect.
            close_error: Exception | None = None
            try:
                page.close()
            except Exception as exc:  # a crashed page must not skip the verdict
                close_error = exc
            if goto_error is not None:
                blocked = [f"[goto-failed] {goto_error}"]
                others = []
                # `not-measured`, NOT `no-tag`: this surface never loaded, so the
                # run did not observe an absent tag and must not report one.
                beacon_label, beacon_findings = "not-measured", []
            else:
                blocked = _violations(console, failed)
                if close_error is not None:
                    blocked.append(f"[close-failed] {close_error}")
                # A response with NO policy refuses nothing, so a total CSP loss
                # would otherwise read as `OK NONE` and exit 0. Absence is a
                # failure, not a label.
                if not csp:
                    blocked.append("[no-csp] response carried NO Content-Security-Policy")
                others = _other_errors(console, failed)
                # Read AFTER the page ended, for the same reason the violation
                # lists are: the RUM POST goes out on `visibilitychange`.
                beacon_label, beacon_findings = _beacon_verdict(
                    requests, csp, tag_present, landed_url, blocked
                )
            # Context cleanup only, AFTER the verdict is computed, so a cleanup
            # failure can never suppress a surface's verdict.
            ctx.close()
            status = "OK" if not blocked else f"{len(blocked)} BLOCKED"
            print(f"{status:>10}  {kind:<8} {url}  {beacon_label}")
            for b in blocked:
                print(f"            - {b[:220]}")
            for f in beacon_findings:
                print(f"            - {f[:220]}")
            # Printed separately, never merged into the verdict.
            for o in others:
                print(f"            (non-CSP error) {o[:200]}")
            labels.append(beacon_label)
            dirty += 1 if blocked else 0
            beacon_dirty += 1 if beacon_findings else 0
        browser.close()

    no_tag, not_measured = _count_labels(labels)

    print(f"\n{dirty} of {len(urls)} surface(s) reported a CSP violation")
    print(
        f"{beacon_dirty} of {len(urls)} surface(s) reported a beacon finding "
        f"(the edge-injected RUM must stay same-origin, {BEACON_RUM_PATH})"
    )
    # NOT a failure (#4706's keep-or-drop decision is open), but loud: a CHANGED
    # injection format and a deliberately disabled zone look identical here, and
    # the format change is one of the failure modes #4762 names. Silence would
    # make it indistinguishable from a healthy run.
    print(
        f"{no_tag} of {len(urls)} surface(s) carried NO injected beacon tag "
        "(zone setting, or the edge changed its injection format — #4706/#4762)"
    )
    if not_measured:
        # Separate line, NOT a share of the no-tag count: nothing was measured on
        # these surfaces (they are already red for [goto-failed] above).
        print(
            f"{not_measured} of {len(urls)} surface(s) were NOT MEASURED "
            "(navigation failed — see the [goto-failed] line; not counted as no-tag)"
        )
    return 1 if (dirty or beacon_dirty) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
