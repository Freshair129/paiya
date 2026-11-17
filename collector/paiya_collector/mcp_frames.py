"""Synchronous same-origin frame security scan; child cards are never read."""

import json

from paiya_collector.browser_adapter import (
    CHALLENGE_TEXT_MARKERS,
    LOGIN_TEXT_MARKERS,
    MARKER_CASEFOLD_OVERRIDES,
)
from paiya_collector.browser_runtime import _READ_BODY, _READ_SNAPSHOT

READ_FRAMES = rf"""(expectedCount) => {{
    const documents = [], texts = [];
    let length = 0, count = 0;
    const visit = (document, location, depth, frameWindow) => {{
        if (depth > 4 || document.readyState !== 'complete' ||
            document.querySelector('object,embed,fencedframe'))
            throw Error('embedded document');
        const frames = document.querySelectorAll('iframe,frame');
        // Closed shadow roots and other unenumerable frame trees cannot be scanned.
        if (frames.length !== frameWindow.length) throw Error('unverified frame tree');
        for (const frame of frames) {{
            if (++count > 16) throw Error('frame count');
            const child = frame.contentDocument, win = frame.contentWindow;
            if (!child || !win || !child.body || child.readyState !== 'complete')
                throw Error('opaque or loading frame');
            const url = win.location.href;
            if (!url.startsWith('about:') && new URL(url).origin !== globalThis.location.origin)
                throw Error('foreign frame');
            const body = ((document, location) =>
                ({_READ_BODY})({{url, selectors: {{}}}}))(child, win.location);
            if (!body || body.truncated || (length += body.text.length) > 100000)
                throw Error('frame security bound');
            texts.push(body.text);
            // URL markers matter even if the child challenge contains no text.
            if (/\/(login|checkpoint|challenge|security)(\/|\?|$)/i.test(new URL(url).pathname))
                texts.push('Security check');
            documents.push(child);
            visit(child, win.location, depth + 1, win);
        }}
    }};
    try {{ visit(document, location, 0, window); }} catch (_) {{ return {{failed: true}}; }}
    if (expectedCount !== undefined && documents.length !== expectedCount)
        return {{failed: true}};
    const text = texts.join('\n');
    const overrides = {json.dumps(MARKER_CASEFOLD_OVERRIDES)};
    const folded = Array.from(text, char => overrides[char] ??
        (/^[A-Z]$/.test(char) ? char.toLowerCase() : char)).join('');
    const markers = {json.dumps(CHALLENGE_TEXT_MARKERS + LOGIN_TEXT_MARKERS)};
    return {{documents, text, challenge: markers.some(marker => folded.includes(marker))}};
}}"""

READ_SAFE_SNAPSHOT = rf"""args => {{
    const frames = ({READ_FRAMES})(args.frameCount);
    if (frames.failed) return {{childDocument: true}};
    const preflight = globalThis.__paiyaFramePreflight;
    if (!preflight || preflight.document !== document ||
        frames.documents.length !== preflight.children.length ||
        !frames.documents.every((doc, i) => doc === preflight.children[i]))
        return {{childDocument: true}};
    if (frames.challenge) return {{body: {{text: frames.text, truncated: false}}, cards: null}};
    const read = ({_READ_SNAPSHOT})(args);
    if (read?.body) read.body.text += '\n' + frames.text;
    return read;
}}"""
