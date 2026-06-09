#!/usr/bin/env python3
"""chatgpt-chat runner.

Deterministic ChatGPT Web automation through OpenClaw 2026.6 CDP transport.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from typing import Any, List, Optional
from websockets.sync.client import connect as websocket_connect

CHATGPT_URL = 'https://chatgpt.com/'
DEFAULT_PROFILE = 'openclaw'
DEFAULT_CDP_URL = 'http://127.0.0.1:18800'


@dataclass
class Source:
    text: str
    href: str


@dataclass
class Request:
    prompt: str
    mode: str = 'fetch-with-sources'
    require_sources: bool = True
    save_report: bool = False
    report_path: Optional[str] = None
    title: Optional[str] = None
    conversation_url: Optional[str] = None
    timeout_seconds: int = 45
    page_ready_timeout_seconds: int = 180
    submit_timeout_seconds: int = 60
    submit_retry_after_seconds: float = 10.0
    recovery_timeout_seconds: int = 180
    recovery_poll_ms: int = 3000
    profile: str = DEFAULT_PROFILE
    tab_label: str = 'chatgpt-monitor'
    browser_base_url: Optional[str] = None
    cdp_url: Optional[str] = None
    browser_token: Optional[str] = None
    browser_password: Optional[str] = None


@dataclass
class Result:
    ok: bool
    mode: str
    prompt: str
    wrapped_prompt: str
    answer: str = ''
    conversationUrl: Optional[str] = None
    title: Optional[str] = None
    sources: List[Source] = field(default_factory=list)
    reportPath: Optional[str] = None
    error: Optional[str] = None
    errorCode: Optional[str] = None
    pageState: Optional[str] = None
    authState: Optional[str] = None
    pageBlockReason: Optional[str] = None
    extractionMode: Optional[str] = None
    usedClipboard: Optional[bool] = None
    copyInterceptWorked: Optional[bool] = None
    browserProfile: Optional[str] = None
    browserTarget: Optional[str] = None
    recoveredFromBlock: bool = False
    notificationNeeded: bool = False
    notificationStage: Optional[str] = None
    notificationMessage: Optional[str] = None
    nextStep: Optional[str] = None
    partial: bool = False
    debug: dict[str, Any] = field(default_factory=dict)


class BrowserClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        cdp_url: Optional[str] = None,
        token: Optional[str] = None,
        password: Optional[str] = None,
    ):
        # OpenClaw 2026.6.x exposes the dedicated "openclaw" browser through
        # Chrome DevTools Protocol is the only supported transport for this
        # private OpenClaw 2026.6.x skill.
        self.cdp_url = (cdp_url or base_url or os.environ.get('OPENCLAW_CDP_URL') or DEFAULT_CDP_URL).rstrip('/')
        self.base_url = self.cdp_url
        self.browser_ws_url: Optional[str] = None
        self.transport = 'cdp'
        self.auth_source = 'none'
        self._next_id = 0

    def request_json(self, path: str, timeout: int = 20) -> Any:
        url = f'{self.cdp_url}{path}'
        req = urllib.request.Request(url, method='GET')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode('utf-8')
                return json.loads(raw) if raw else None
        except TimeoutError as e:
            raise RuntimeError(f'CDP request timed out after {timeout}s: GET {path}') from e
        except urllib.error.HTTPError as e:
            payload = e.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'CDP HTTP {e.code}: {payload}') from e
        except urllib.error.URLError as e:
            raise RuntimeError(f'CDP request failed: {e}') from e

    def health(self) -> dict[str, Any]:
        version = self.request_json('/json/version', timeout=5)
        if not isinstance(version, dict) or not version.get('webSocketDebuggerUrl'):
            raise RuntimeError(f'CDP endpoint did not return a browser websocket URL: {version}')
        self.browser_ws_url = str(version['webSocketDebuggerUrl'])
        return version

    def _send_cdp(self, ws_url: str, method: str, params: Optional[dict[str, Any]] = None, timeout: int = 20) -> Any:
        self._next_id += 1
        message_id = self._next_id
        payload = {'id': message_id, 'method': method, 'params': params or {}}
        try:
            with websocket_connect(ws_url, open_timeout=timeout, close_timeout=2) as ws:
                ws.send(json.dumps(payload))
                deadline = time.time() + timeout
                while time.time() < deadline:
                    raw = ws.recv(timeout=max(0.1, deadline - time.time()))
                    data = json.loads(raw)
                    if data.get('id') != message_id:
                        continue
                    if data.get('error'):
                        raise RuntimeError(f'CDP {method} failed: {data["error"]}')
                    return data.get('result')
        except TimeoutError as e:
            raise RuntimeError(f'CDP {method} timed out after {timeout}s') from e
        raise RuntimeError(f'CDP {method} did not return a response')

    def _browser_ws(self) -> str:
        if not self.browser_ws_url:
            self.health()
        assert self.browser_ws_url is not None
        return self.browser_ws_url

    def _page_ws(self, target_id: str) -> str:
        deadline = time.time() + 5
        while time.time() < deadline:
            for tab in self.tabs(DEFAULT_PROFILE):
                if tab.get('targetId') == target_id and tab.get('wsUrl'):
                    return str(tab['wsUrl'])
            time.sleep(0.2)
        raise RuntimeError(f'CDP target not found or missing websocket URL: {target_id}')

    def open_tab(self, url: str, profile: str, label: Optional[str] = None) -> dict[str, Any]:
        target = self._send_cdp(self._browser_ws(), 'Target.createTarget', {'url': url}, timeout=30)
        target_id = str((target or {}).get('targetId') or '')
        if not target_id:
            raise RuntimeError(f'CDP Target.createTarget returned no targetId: {target}')
        return {
            'targetId': target_id,
            'id': target_id,
            'tabId': target_id,
            'suggestedTargetId': label or target_id,
            'label': label,
            'url': url,
            'type': 'page',
            'wsUrl': self._page_ws(target_id),
        }

    def tabs(self, profile: str) -> list[dict[str, Any]]:
        tabs = self.request_json('/json/list', timeout=10)
        out: list[dict[str, Any]] = []
        for tab in tabs or []:
            if not isinstance(tab, dict) or tab.get('type') != 'page':
                continue
            target_id = str(tab.get('id') or '')
            out.append({
                'targetId': target_id,
                'id': target_id,
                'tabId': target_id,
                'suggestedTargetId': target_id,
                'label': None,
                'title': tab.get('title') or '',
                'url': tab.get('url') or '',
                'type': tab.get('type') or 'page',
                'wsUrl': tab.get('webSocketDebuggerUrl'),
            })
        return out

    def snapshot(self, *, target_id: str, profile: str, max_chars: int = 12000, refs: str = 'aria', fmt: str = 'aria', include_urls: bool = True) -> dict[str, Any]:
        fn = """() => ({
          url: location.href,
          title: document.title || '',
          text: (document.body && document.body.innerText || '').slice(0, 12000),
          urls: Array.from(document.querySelectorAll('a[href]')).map(a => a.href).filter(Boolean).slice(0, 200)
        })"""
        result = self.act(profile=profile, payload={'kind': 'evaluate', 'targetId': target_id, 'fn': fn})
        value = result.get('result') if isinstance(result, dict) else result
        if isinstance(value, dict):
            return value
        return {'url': None, 'title': None, 'text': '', 'urls': []}

    def act(self, *, profile: str, payload: dict[str, Any]) -> dict[str, Any]:
        kind = payload.get('kind')
        target_id = str(payload.get('targetId') or '')
        if kind == 'wait':
            time.sleep(float(payload.get('timeMs') or 0) / 1000.0)
            return {'ok': True}
        if kind != 'evaluate':
            raise RuntimeError(f'Unsupported CDP browser action kind: {kind}')
        fn = str(payload.get('fn') or '')
        expression = f'({fn})()'
        cdp_result = self._send_cdp(
            self._page_ws(target_id),
            'Runtime.evaluate',
            {
                'expression': expression,
                'awaitPromise': True,
                'returnByValue': True,
                'userGesture': True,
            },
            timeout=30,
        )
        if cdp_result and cdp_result.get('exceptionDetails'):
            raise RuntimeError(f'CDP Runtime.evaluate exception: {cdp_result["exceptionDetails"]}')
        remote = (cdp_result or {}).get('result') or {}
        if 'value' in remote:
            return {'result': remote.get('value')}
        return {'result': remote.get('description') or remote.get('unserializableValue')}

    def close_tab(self, target_id: str, profile: str) -> None:
        self._send_cdp(self._browser_ws(), 'Target.closeTarget', {'targetId': target_id}, timeout=10)



def wrap_prompt(req: Request) -> str:
    if req.mode == 'search':
        return (
            'Answer in English. Use web browsing/search if available. '
            'Answer the question below and include a "Sources" section listing the main sources '
            f'or references you relied on, with links where available.\n\nQuestion: {req.prompt}'
        )
    if req.mode == 'report':
        return (
            'Answer in English. Use web browsing/search if available. '
            'Write a concise, structured report for the question below. '
            'End with a "Sources" section listing the main sources or references you relied on, '
            f'with links where available.\n\nQuestion: {req.prompt}'
        )
    if req.mode == 'fetch-with-sources':
        return (
            'Answer in English. Answer the question below and include a "Sources" section '
            f'listing the main sources or references you relied on, with links where available.\n\nQuestion: {req.prompt}'
        )
    return req.prompt


_slug_re = re.compile(r'[^a-zA-Z0-9\u4e00-\u9fff]+')


def slugify(text: str, limit: int = 48) -> str:
    text = _slug_re.sub('-', text).strip('-').lower()
    text = re.sub(r'-+', '-', text)
    return text[:limit] or 'chatgpt-chat-report'


def dedupe_sources(sources: List[Source]) -> List[Source]:
    seen = set()
    out: List[Source] = []
    for src in sources:
        href = src.href.strip()
        if not href or href in seen:
            continue
        seen.add(href)
        out.append(Source(text=normalize_source_text(src.text, href), href=href))
    return out


def clean_answer_text(text: str) -> str:
    text = (text or '').strip()
    text = re.sub(r'^ChatGPT\s*说：\s*', '', text)
    text = re.sub(r'^ChatGPT\s*says:\s*', '', text, flags=re.I)
    text = re.sub(r'^ChatGPT\s*said:\s*', '', text, flags=re.I)
    tail_patterns = [
        r'\n*来源\s*Is this conversation helpful so far\?\s*$',
        r'\n*Is this conversation helpful so far\?\s*$',
        r'\n*来源\s*你喜欢此风格吗？\s*$',
        r'\n*你喜欢此风格吗？\s*$',
        r'\n*如果需要，我可以再补充：[\s\S]*$',
        r'\n*如果你愿意，我也可以再用[\s\S]*$',
        r'\n*Do you like this personality\?\s*$'
    ]
    for pattern in tail_patterns:
        text = re.sub(pattern, '', text, flags=re.I)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def normalize_source_text(text: str, href: str) -> str:
    text = (text or '').strip()
    text = re.sub(r'\s*\+\d+\s*$', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return href
    parts = [part.strip() for part in re.split(r'\s{2,}|(?<!\w)(?=Investopedia|CoinGecko|CoinMarketCap|MarketWatch|The Economic Times|巴伦周刊|市场观察|雅虎财经)', text) if part.strip()]
    if len(parts) > 1:
        text = max(parts, key=len)
    text = re.sub(r'\s*\+\d+\s*$', '', text).strip()
    return text or href


def render_report(result: Result) -> str:
    lines = []
    title = result.title or 'ChatGPT Chat Report'
    lines.append(f'# {title}')
    lines.append('')
    lines.append('> Generated by `chatgpt-chat` local skill')
    meta = []
    if result.conversationUrl:
        meta.append(f'Conversation: `{result.conversationUrl}`')
    if result.extractionMode:
        meta.append(f'Extraction mode: `{result.extractionMode}`')
    if meta:
        for item in meta:
            lines.append(f'> {item}')
        lines.append('')
    lines.append('## Prompt')
    lines.append('')
    lines.append(result.prompt)
    lines.append('')
    lines.append('## Answer')
    lines.append('')
    lines.append(result.answer.strip() or '_No answer captured._')
    if result.sources:
        lines.append('')
        lines.append('## Sources')
        lines.append('')
        for src in result.sources:
            lines.append(f'- [{src.text}]({src.href})')
    lines.append('')
    return '\n'.join(lines)


def save_report(result: Result, requested_path: Optional[str]) -> str:
    if requested_path:
        out = Path(requested_path).expanduser()
        if not out.is_absolute():
            out = Path.cwd() / out
    else:
        base = slugify(result.title or result.prompt)
        out = Path.cwd() / f'{base}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(result), encoding='utf-8')
    return str(out)


def _evaluate(client: BrowserClient, profile: str, target_id: str, fn: str) -> Any:
    resp = client.act(profile=profile, payload={'kind': 'evaluate', 'targetId': target_id, 'fn': fn})
    if isinstance(resp, dict):
        for key in ('result', 'value', 'returnValue'):
            if key in resp:
                return resp.get(key)
    return resp


def _wait(client: BrowserClient, profile: str, target_id: str, ms: int) -> None:
    client.act(profile=profile, payload={'kind': 'wait', 'targetId': target_id, 'timeMs': ms})


def _detect_page_state(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    fn = r"""() => {
      const text = (document.body?.innerText || '').trim();
      const href = location.href;
      const title = (document.title || '').trim();
      const textbox = document.querySelector('#prompt-textarea, [data-testid="prompt-textarea"], [role="textbox"], [contenteditable="true"]');
      const loginSignals = [
        'Log in', 'Sign up', '登录', '注册', 'Continue with Google', 'Continue with Apple',
        'session expired', 'logged out'
      ];
      const verificationSignals = [
        'Verify you are human', 'Just a moment', 'Cloudflare', 'Turnstile',
        '验证你是真人', '请稍候'
      ];
      const blockedSignals = [
        'Unable to load', 'Something went wrong', 'Access denied', '请求过于频繁',
        '暂时无法使用', 'We detect suspicious activity'
      ];
      const hasLogin = loginSignals.some(v => text.includes(v));
      const hasVerification = verificationSignals.some(v => text.includes(v));
      const hasBlocked = blockedSignals.some(v => text.includes(v));
      let state = 'unknown';
      if (textbox) state = 'ready';
      else if (hasVerification) state = 'human_verification';
      else if (hasLogin || href.includes('/auth') || href.includes('login')) state = 'login_required';
      else if (hasBlocked) state = 'blocked';
      const authState = state === 'ready'
        ? 'authenticated-or-usable'
        : hasLogin ? 'guest-or-login-suggested' : 'authenticated-or-unknown';
      const loginMatched = loginSignals.filter(v => text.includes(v)).slice(0, 5);
      const verificationMatched = verificationSignals.filter(v => text.includes(v)).slice(0, 5);
      const blockedMatched = blockedSignals.filter(v => text.includes(v)).slice(0, 5);
      const reason = state === 'human_verification'
        ? (verificationMatched[0] || null)
        : state === 'login_required'
          ? (loginMatched[0] || null)
          : state === 'blocked'
            ? (blockedMatched[0] || null)
            : null;
      return {
        ok: true,
        state,
        authState,
        href,
        title,
        reason,
        hasTextbox: !!textbox,
        loginMatched,
        verificationMatched,
        blockedMatched,
        textPreview: text.slice(0, 800)
      };
    }"""
    return _evaluate(client, req.profile, target_id, fn)


def _page_state_debug_sample(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {'ok': False, 'error': 'state was not an object'}
    return {
        'ts': int(time.time()),
        'ok': state.get('ok'),
        'state': state.get('state'),
        'authState': state.get('authState'),
        'href': state.get('href'),
        'title': state.get('title'),
        'reason': state.get('reason'),
        'hasTextbox': state.get('hasTextbox'),
        'loginMatched': (state.get('loginMatched') or [])[:5],
        'verificationMatched': (state.get('verificationMatched') or [])[:5],
        'blockedMatched': (state.get('blockedMatched') or [])[:5],
        'textPreview': (state.get('textPreview') or '')[:240],
    }


def _wait_for_page_ready_state(
    client: BrowserClient,
    req: Request,
    target_id: str,
    debug: dict[str, Any],
    *,
    prefix: str = '',
) -> dict[str, Any]:
    started = time.time()
    timeout_seconds = max(1, req.page_ready_timeout_seconds)
    deadline = started + timeout_seconds
    checks: list[dict[str, Any]] = []
    last_state: dict[str, Any] | None = None
    last_error: str | None = None

    while time.time() < deadline:
        try:
            state = _detect_page_state(client, req, target_id)
            last_state = state
            sample = _page_state_debug_sample(state)
            checks.append(sample)
            checks = checks[-20:]
            page_state = state.get('state') if isinstance(state, dict) else None
            if page_state in {'ready', 'login_required', 'human_verification', 'blocked'}:
                break
        except Exception as exc:
            last_error = str(exc)
            checks.append({'ts': int(time.time()), 'ok': False, 'error': last_error})
            checks = checks[-20:]
        _wait(client, req.profile, target_id, 2000)

    elapsed = round(time.time() - started, 3)
    wait_debug = {
        'timeoutSeconds': timeout_seconds,
        'elapsedSeconds': elapsed,
        'checks': checks,
        'lastError': last_error,
        'finalState': _page_state_debug_sample(last_state),
    }
    key = f'{prefix}pageStateWait' if prefix else 'pageStateWait'
    debug[key] = wait_debug
    debug[f'{prefix}pageStateChecks' if prefix else 'pageStateChecks'] = checks

    if isinstance(last_state, dict):
        return last_state
    return {
        'ok': False,
        'state': 'unknown',
        'authState': 'authenticated-or-unknown',
        'href': None,
        'title': None,
        'reason': last_error,
        'hasTextbox': False,
        'loginMatched': [],
        'verificationMatched': [],
        'blockedMatched': [],
        'textPreview': '',
    }


def _ensure_prompt_injected(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    prompt_json = json.dumps(wrap_prompt(req), ensure_ascii=False)
    fn = f"""() => {{
      const el = document.querySelector('#prompt-textarea, [data-testid="prompt-textarea"], [role="textbox"], [contenteditable="true"]');
      if (!el) return {{ok:false, reason:'no textbox', method:null}};
      const text = {prompt_json};
      el.focus();
      try {{
        if (el.isContentEditable) {{
          el.textContent = '';
          el.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: '', inputType: 'deleteContentBackward' }}));
          for (const chunk of text.match(/.{{1,80}}/g) || []) {{
            el.textContent += chunk;
            el.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: chunk, inputType: 'insertText' }}));
          }}
        }} else {{
          el.value = text;
          el.dispatchEvent(new Event('input', {{ bubbles: true }}));
          el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
      }} catch (err) {{
        if (el.isContentEditable) {{
          el.textContent = text;
          el.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: text, inputType: 'insertText' }}));
        }} else {{
          el.value = text;
          el.dispatchEvent(new Event('input', {{ bubbles: true }}));
          el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
        return {{ok:true, method:'inject-fallback', warning:String(err)}};
      }}
      return {{ok:true, method: el.isContentEditable ? 'contenteditable-chunked' : 'value-set'}};
    }}"""
    return _evaluate(client, req.profile, target_id, fn)


def _find_send_button(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    fn = """() => {
      const btn = document.querySelector('[data-testid="send-button"]') || [...document.querySelectorAll('button')].find(b => {
        const text = (b.getAttribute('aria-label') || b.innerText || '').toLowerCase();
        return text.includes('发送') || text.includes('send');
      });
      return {ok: !!btn, found: btn ? (btn.getAttribute('aria-label') || btn.innerText || '') : null};
    }"""
    return _evaluate(client, req.profile, target_id, fn)


def _click_send(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    fn = """() => {
      const btn = document.querySelector('[data-testid="send-button"]') || [...document.querySelectorAll('button')].find(b => {
        const text = (b.getAttribute('aria-label') || b.innerText || '').toLowerCase();
        return text.includes('发送') || text.includes('send');
      });
      if (!btn) return {ok:false, reason:'send button missing'};
      btn.click();
      return {ok:true};
    }"""
    return _evaluate(client, req.profile, target_id, fn)


def _submission_state(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    fn = """() => {
      const editor = document.querySelector('#prompt-textarea, [data-testid="prompt-textarea"], [role="textbox"], [contenteditable="true"]');
      const editorText = editor ? ((editor.innerText || editor.textContent || editor.value || '').trim()) : '';
      const send = document.querySelector('[data-testid="send-button"]') || [...document.querySelectorAll('button')].find(b => {
        const text = (b.getAttribute('aria-label') || b.innerText || '').toLowerCase();
        return text.includes('发送') || text.includes('send');
      });
      const stop = document.querySelector('[data-testid="stop-button"]') || [...document.querySelectorAll('button')].find(b => {
        const text = (b.getAttribute('aria-label') || b.innerText || '').toLowerCase();
        return text.includes('stop') || text.includes('停止');
      });
      const assistantCount = document.querySelectorAll('[data-message-author-role="assistant"]').length;
      return {
        ok: true,
        href: location.href,
        title: document.title || '',
        hasEditor: !!editor,
        editorTextLength: editorText.length,
        editorTextPreview: editorText.slice(0, 160),
        hasSendButton: !!send,
        sendDisabled: !!(send && (send.disabled || send.getAttribute('aria-disabled') === 'true')),
        hasStopButton: !!stop,
        assistantCount
      };
    }"""
    return _evaluate(client, req.profile, target_id, fn)


def _fallback_submit(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    fn = """() => {
      const send = document.querySelector('[data-testid="send-button"]') || [...document.querySelectorAll('button')].find(b => {
        const text = (b.getAttribute('aria-label') || b.innerText || '').toLowerCase();
        return text.includes('发送') || text.includes('send');
      });
      if (send && !(send.disabled || send.getAttribute('aria-disabled') === 'true')) {
        send.click();
        return {ok:true, method:'reclick-send'};
      }
      const editor = document.querySelector('#prompt-textarea, [data-testid="prompt-textarea"], [role="textbox"], [contenteditable="true"]');
      if (!editor) return {ok:false, method:'none', reason:'editor missing'};
      editor.focus();
      editor.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', which:13, keyCode:13, bubbles:true}));
      editor.dispatchEvent(new KeyboardEvent('keypress', {key:'Enter', code:'Enter', which:13, keyCode:13, bubbles:true}));
      editor.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter', code:'Enter', which:13, keyCode:13, bubbles:true}));
      return {ok:true, method:'enter-fallback'};
    }"""
    return _evaluate(client, req.profile, target_id, fn)


def _extract_answer_and_sources(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    fn = r"""async () => {
      function toMd(el, listPrefix = '') {
        if (el.nodeType === 3) return el.textContent;
        if (el.nodeType !== 1) return '';
        const tag = el.tagName.toLowerCase();
        
        if (tag === 'pre') {
            const codeNode = el.querySelector('code');
            let lang = '';
            const header = el.querySelector('.flex.items-center, .flex.items-center.text-xs');
            if (header) {
                lang = (header.textContent || '').replace(/Copy code|复制代码|copy/ig, '').trim();
            } else if (codeNode && codeNode.className) {
                const match = codeNode.className.match(/language-(\\w+)/);
                if (match) lang = match[1];
            }
            const text = codeNode ? (codeNode.innerText || codeNode.textContent) : (el.innerText || el.textContent);
            return '\\n\\n```' + lang + '\\n' + text.trim() + '\\n```\\n\\n';
        }
        
        if (tag === 'button' || tag === 'svg' || (el.closest && el.closest('button'))) return '';
        
        let res = '';
        let index = 1;
        for (const child of el.childNodes) {
            if (tag === 'ol' && child.tagName && child.tagName.toLowerCase() === 'li') {
                res += toMd(child, index + '. ');
                index++;
            } else if (tag === 'ul' && child.tagName && child.tagName.toLowerCase() === 'li') {
                res += toMd(child, '- ');
            } else {
                res += toMd(child, listPrefix);
            }
        }
        
        if (tag === 'p') return res + '\\n\\n';
        if (tag === 'h1') return '\\n\\n# ' + res + '\\n\\n';
        if (tag === 'h2') return '\\n\\n## ' + res + '\\n\\n';
        if (tag === 'h3') return '\\n\\n### ' + res + '\\n\\n';
        if (tag === 'h4') return '\\n\\n#### ' + res + '\\n\\n';
        if (tag === 'strong' || tag === 'b') return '**' + res + '**';
        if (tag === 'em' || tag === 'i') return '*' + res + '*';
        if (tag === 'code') return '`' + res + '`';
        if (tag === 'a') return '[' + res + '](' + (el.href || '') + ')';
        if (tag === 'li') return '\\n' + listPrefix + res.trim() + '\\n';
        if (tag === 'blockquote') return '\\n\\n> ' + res.trim().replace(/\\n/g, '\\n> ') + '\\n\\n';
        
        return res;
      }

      function pickSourceRoot(roleElement, role) {
        const selectors = role === 'assistant'
          ? ['.markdown', '.prose', '.whitespace-pre-wrap']
          : ['.whitespace-pre-wrap', '.markdown', '.prose'];
        for (const selector of selectors) {
          const candidate = roleElement.querySelector(selector);
          if (candidate) return candidate;
        }
        return roleElement;
      }

      function uniqueNodes(nodes) {
        const seen = new Set();
        const out = [];
        for (const node of nodes) {
          if (!node || seen.has(node)) continue;
          seen.add(node);
          out.push(node);
        }
        return out;
      }

      const roleAssistants = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'))
        .filter(node => (node.innerText || node.textContent || '').trim().length > 8);
      let assistantRole = roleAssistants.length ? roleAssistants[roleAssistants.length - 1] : null;
      let assistant = assistantRole ? pickSourceRoot(assistantRole, 'assistant') : null;
      let actionRoot = assistantRole || assistant;

      if (!assistant) {
        const fallbackNodes = uniqueNodes([
          ...document.querySelectorAll('article'),
          ...document.querySelectorAll('[data-testid*="assistant"]'),
          ...document.querySelectorAll('[data-testid*="conversation-turn"]'),
          ...document.querySelectorAll('[data-testid*="message"]'),
          ...document.querySelectorAll('.markdown, .prose, .whitespace-pre-wrap')
        ]).filter(node => {
          const text = (node.innerText || node.textContent || '').trim();
          if (!text || text.length < 8) return false;
          if (node.matches && node.matches('nav, aside, header, footer, form')) return false;
          if (node.closest && node.closest('nav, aside, header, footer, form')) return false;
          if (text.includes('What is the best budget dash cam?') && text.length < 400) return false;
          return true;
        });
        assistant = fallbackNodes.length ? fallbackNodes[fallbackNodes.length - 1] : null;
        actionRoot = assistant;
      }

      if (!assistant) {
        return {
          ok:false,
          reason:'no assistant message node',
          diagnostics: {
            articleCount: document.querySelectorAll('article').length,
            assistantRoleCount: document.querySelectorAll('[data-message-author-role="assistant"]').length,
            userRoleCount: document.querySelectorAll('[data-message-author-role="user"]').length,
            markdownCount: document.querySelectorAll('.markdown').length,
            proseCount: document.querySelectorAll('.prose').length,
            messageTestIdCount: document.querySelectorAll('[data-testid*="message"], [data-testid*="conversation-turn"]').length,
            bodyTail: (document.body?.innerText || '').trim().slice(-1200)
          }
        };
      }
      
      const heading = assistant.querySelector('h1,h2,h3,h4');
      const title = (document.title || '').trim() || (heading?.innerText || '').trim() || null;
      
      const links = [...assistant.querySelectorAll('a[href]')].map(a => ({
        text: (a.innerText || a.textContent || '').trim(),
        href: a.href
      }));
      const pageText = document.body?.innerText || '';
      const streamingMarkers = [
        'ChatGPT 仍在生成回复',
        '仍在生成回复',
        '停止生成',
        '停止回答',
        'Stop generating',
        'Stop responding'
      ];
      const isStreaming = streamingMarkers.some(marker => pageText.includes(marker));
      const actionButtons = [...(actionRoot || assistant).querySelectorAll('button,[role="button"]')];
      const labels = actionButtons.map(b => ((b.getAttribute('aria-label') || b.innerText || b.textContent || '').trim())).filter(Boolean);
      const testIds = actionButtons.map(b => (b.getAttribute('data-testid') || '').trim()).filter(Boolean);

      function norm(v) {
        return (v || '').replace(/\s+/g, ' ').trim().toLowerCase();
      }

      function isTurnLevelCopyButton(btn) {
        const label = norm(btn.getAttribute('aria-label') || btn.innerText || btn.textContent || '');
        const testId = norm(btn.getAttribute('data-testid') || '');
        if (label.includes('copy code') || label.includes('复制代码')) return false;
        if (btn.closest('pre, code')) return false;
        if (testId === 'copy-turn-action-button') return true;
        if (!(label === 'copy' || label === '复制' || label.includes('copy response') || label.includes('复制回答'))) return false;

        const container = btn.parentElement;
        if (!container) return false;
        const siblingButtons = [...container.querySelectorAll('button,[role="button"]')];
        const siblingSignals = siblingButtons.map(b => norm(b.getAttribute('aria-label') || b.innerText || b.textContent || '') + ' ' + norm(b.getAttribute('data-testid') || ''));
        const hasTurnActions = siblingSignals.some(v =>
          v.includes('good response') || v.includes('bad response') || v.includes('share') ||
          v.includes('retry') || v.includes('regenerate') || v.includes('thumbs-up') || v.includes('thumbs-down') ||
          v.includes('喜欢') || v.includes('不喜欢') || v.includes('分享') || v.includes('重试')
        );
        return hasTurnActions;
      }

      const turnLevelCopyButtons = actionButtons.filter(isTurnLevelCopyButton);
      const copyBtn = turnLevelCopyButtons.length ? turnLevelCopyButtons[turnLevelCopyButtons.length - 1] : null;
      
      const hasCopyButton = !!copyBtn;
      const hasPositiveFeedback = labels.some(v => /(good response|good|赞|顶|有帮助)/i.test(v)) || testIds.some(v => /thumbs-up|good-response/i.test(v));
      const hasNegativeFeedback = labels.some(v => /(bad response|bad|踩|没帮助)/i.test(v)) || testIds.some(v => /thumbs-down|bad-response/i.test(v));

      // Try copy-path extraction first when the copy button is present
      let text = '';
      let usedClipboard = false;
      let copyInterceptWorked = false;
      let copyMethod = null;
      let copyError = null;
      
      if (copyBtn && !isStreaming) {
          return new Promise(resolve => {
              let intercepted = null;
              const originalWriteText = navigator.clipboard && navigator.clipboard.writeText
                ? navigator.clipboard.writeText.bind(navigator.clipboard)
                : null;

              if (navigator.clipboard && navigator.clipboard.writeText) {
                  navigator.clipboard.writeText = async (clipboardData) => {
                      intercepted = clipboardData;
                      if (originalWriteText) {
                          try { return await originalWriteText(clipboardData); } catch (_) { return Promise.resolve(); }
                      }
                      return Promise.resolve();
                  };
              }
              
              copyBtn.click();
              
              setTimeout(async () => {
                  try {
                      if (navigator.clipboard && navigator.clipboard.writeText && originalWriteText) {
                          navigator.clipboard.writeText = originalWriteText;
                      }

                      if (navigator.clipboard && navigator.clipboard.readText) {
                          try {
                              const clipText = await Promise.race([
                                  navigator.clipboard.readText(),
                                  new Promise((_, reject) => setTimeout(() => reject(new Error('clipboard-read-timeout')), 1200))
                              ]);
                              if (clipText && String(clipText).trim()) {
                                  text = String(clipText);
                                  usedClipboard = true;
                                  copyInterceptWorked = true;
                                  copyMethod = 'clipboard-read';
                              }
                          } catch (err) {
                              copyError = 'clipboard-read:' + String(err);
                          }
                      }

                      if (!text && intercepted && String(intercepted).trim()) {
                          text = String(intercepted);
                          usedClipboard = true;
                          copyInterceptWorked = true;
                          copyMethod = 'writeText-intercept';
                      }

                      if (!text) {
                          const contentNode = assistant.querySelector('.markdown') || assistant;
                          const rawText = toMd(contentNode);
                          text = rawText.replace(/\\n{3,}/g, '\\n\\n').trim();
                          copyMethod = 'dom-fallback';
                      }
                  } finally {
                      resolve({ ok:true, title, text, usedClipboard, copyInterceptWorked, copyMethod, copyError, links, url: location.href, isStreaming, hasCopyButton, hasPositiveFeedback, hasNegativeFeedback, labels, testIds });
                  }
              }, 400);
          });
      }

      // Fallback to DOM parsing if copy-path extraction is unavailable or streaming
      if (!text) {
          const contentNode = assistant.querySelector('.markdown') || assistant;
          const rawText = toMd(contentNode);
          text = rawText.replace(/\\n{3,}/g, '\\n\\n').trim();
          copyMethod = 'dom-fallback';
      }

      return { ok:true, title, text, usedClipboard, copyInterceptWorked, copyMethod, copyError, links, url: location.href, isStreaming, hasCopyButton, hasPositiveFeedback, hasNegativeFeedback, labels, testIds };
    }"""
    return _evaluate(client, req.profile, target_id, fn)


def _target_from_opened(opened: dict[str, Any], fallback_label: Optional[str]) -> str:
    """Use the concrete CDP target id for page actions."""
    for key in ('targetId', 'id', 'tabId', 'suggestedTargetId', 'label'):
        value = opened.get(key)
        if value:
            return str(value)
    if fallback_label:
        return fallback_label
    raise RuntimeError(f'OpenClaw did not return a usable browser target: {opened}')


def _tab_handles(client: BrowserClient, profile: str) -> tuple[list[str], list[dict[str, Any]]]:
    tabs = client.tabs(profile)
    handles: list[str] = []
    for tab in tabs:
        for key in ('suggestedTargetId', 'tabId', 'targetId', 'id', 'label'):
            value = tab.get(key)
            if value:
                handles.append(str(value))
    return handles, tabs


def _wait_until_tab_ready(client: BrowserClient, profile: str, target_id: str, timeout_seconds: int = 8, debug: Optional[dict[str, Any]] = None) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Optional[Exception] = None
    missing_count = 0
    checks: list[dict[str, Any]] = []
    while time.time() < deadline:
        try:
            tab_handles, tabs = _tab_handles(client, profile)
            present = target_id in tab_handles
            checks.append({
                'ts': int(time.time()),
                'targetId': target_id,
                'present': present,
                'count': len(tabs),
                'tabs': [
                    {
                        'suggestedTargetId': t.get('suggestedTargetId'),
                        'tabId': t.get('tabId'),
                        'targetId': t.get('targetId'),
                        'label': t.get('label'),
                        'url': t.get('url'),
                    }
                    for t in tabs[-5:]
                ],
            })
            checks = checks[-8:]
            if not present:
                missing_count += 1
                if missing_count >= 3:
                    raise RuntimeError(f'tab not found during readiness check: {target_id}')
                time.sleep(1.0)
                continue
            _evaluate(client, profile, target_id, '() => ({ready:true, href: location.href})')
            if debug is not None:
                debug['tabReadyChecks'] = checks
                debug['tabMissingCount'] = missing_count
            return
        except Exception as e:
            last_error = e
            time.sleep(1.0)
    if debug is not None:
        debug['tabReadyChecks'] = checks
        debug['tabMissingCount'] = missing_count
    if last_error:
        raise last_error
    raise RuntimeError('tab did not become ready in time')


def _build_block_notification(state: str, timeout_seconds: int) -> str:
    if state == 'login_required':
        return (
            'ChatGPT Web 当前需要重新登录。'
            f' 请在浏览器中恢复登录状态；runner 将在接下来约 {timeout_seconds} 秒内持续等待并自动继续。'
        )
    if state == 'human_verification':
        return (
            'ChatGPT Web 当前触发了人机验证。'
            f' 请在浏览器中完成验证；runner 将在接下来约 {timeout_seconds} 秒内持续等待并自动继续。'
        )
    return (
        'ChatGPT Web 当前处于阻断状态。'
        f' 请在浏览器中检查并处理；runner 将在接下来约 {timeout_seconds} 秒内持续等待并自动继续。'
    )


def _wait_for_manual_recovery(client: BrowserClient, req: Request, target_id: str, initial_state: str) -> dict[str, Any]:
    deadline = time.time() + max(5, req.recovery_timeout_seconds)
    checks: list[dict[str, Any]] = []
    while time.time() < deadline:
        state = _detect_page_state(client, req, target_id)
        checks.append({
            'ts': int(time.time()),
            'state': (state or {}).get('state'),
            'loginMatched': (state or {}).get('loginMatched') or [],
            'verificationMatched': (state or {}).get('verificationMatched') or [],
            'blockedMatched': (state or {}).get('blockedMatched') or [],
        })
        checks = checks[-8:]
        if state and state.get('ok') and state.get('state') == 'ready':
            return {
                'ok': True,
                'recovered': True,
                'state': state,
                'checks': checks,
                'notificationNeeded': True,
                'notificationStage': 'recovered',
                'notificationMessage': 'ChatGPT Web 已恢复到可用状态，runner 将继续执行当前任务。',
            }
        _wait(client, req.profile, target_id, req.recovery_poll_ms)
    final_state = _detect_page_state(client, req, target_id)
    checks.append({
        'ts': int(time.time()),
        'state': (final_state or {}).get('state'),
        'loginMatched': (final_state or {}).get('loginMatched') or [],
        'verificationMatched': (final_state or {}).get('verificationMatched') or [],
        'blockedMatched': (final_state or {}).get('blockedMatched') or [],
    })
    return {
        'ok': False,
        'recovered': False,
        'initialState': initial_state,
        'state': final_state,
        'checks': checks[-8:],
        'notificationNeeded': True,
        'notificationStage': 'blocked-timeout',
        'notificationMessage': _build_block_notification(initial_state, req.recovery_timeout_seconds),
    }


def _safe_close_tab(client: BrowserClient, profile: str, target_id: Optional[str], debug: dict[str, Any], key: str = 'closedTab') -> None:
    if not target_id:
        return
    try:
        client.close_tab(target_id, profile)
        debug[key] = True
    except Exception as close_err:
        debug[key] = False
        debug[f'{key}Error'] = str(close_err)


def _is_transient_browser_open_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            'failed to start chrome cdp',
            'cooling down',
            'http_unreachable',
            'browser launch',
            'fetch failed',
            'timed out',
        )
    )


def _is_cdp_transport_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            'cdp request failed',
            'cdp request timed out',
            'cdp endpoint',
            'target.createtarget',
            'runtime.evaluate',
            'connection refused',
            'failed to establish a new connection',
        )
    )


def _browser_open_retry_delay_seconds(message: str, attempt: int) -> float:
    match = re.search(r'retry in\s+(\d+)s', message, flags=re.I)
    if match:
        return float(match.group(1))
    return 10.0 if attempt == 1 else 30.0


def _open_ready_tab(client: BrowserClient, req: Request, target_url: str, debug: dict[str, Any], result: Result, *, prefix: str = '') -> str:
    attempts: list[dict[str, Any]] = []
    opened: dict[str, Any] | None = None
    for attempt in range(1, 4):
        try:
            opened = client.open_tab(target_url, req.profile, req.tab_label)
            attempts.append({'attempt': attempt, 'ok': True})
            break
        except Exception as exc:
            message = str(exc)
            retryable = _is_transient_browser_open_error(message)
            attempt_info: dict[str, Any] = {
                'attempt': attempt,
                'ok': False,
                'error': message,
                'retryable': retryable,
            }
            if retryable and attempt < 3:
                delay = _browser_open_retry_delay_seconds(message, attempt)
                attempt_info['retryDelaySeconds'] = delay
                attempts.append(attempt_info)
                time.sleep(delay)
                continue
            attempts.append(attempt_info)
            debug[f'{prefix}openTabAttempts' if prefix else 'openTabAttempts'] = attempts
            raise
    debug[f'{prefix}openTabAttempts' if prefix else 'openTabAttempts'] = attempts
    if opened is None:
        raise RuntimeError('Browser tab did not open')
    target_id = _target_from_opened(opened, req.tab_label)
    result.browserTarget = target_id
    debug[f'{prefix}openedTab' if prefix else 'openedTab'] = opened
    debug[f'{prefix}targetId' if prefix else 'targetId'] = target_id
    _wait_until_tab_ready(client, req.profile, target_id, debug=debug)
    _wait(client, req.profile, target_id, 2500)
    return target_id


def _submit_prompt_and_get_conversation_url(client: BrowserClient, req: Request, target_id: str, debug: dict[str, Any], *, prefix: str = '') -> tuple[str | None, dict[str, Any] | None]:
    injected = _ensure_prompt_injected(client, req, target_id)
    debug[f'{prefix}injected' if prefix else 'injected'] = injected
    if not injected or not injected.get('ok'):
        return None, {'errorCode': 'ERR_NO_TEXTBOX', 'error': f'Prompt injection failed: {injected}', 'nextStep': 'Re-check textbox selectors on ChatGPT homepage.'}

    send = _find_send_button(client, req, target_id)
    debug[f'{prefix}sendButton' if prefix else 'sendButton'] = send
    if not send or not send.get('ok'):
        return None, {'errorCode': 'ERR_SEND_BUTTON_MISSING', 'error': 'Send button did not appear after prompt injection.', 'nextStep': 'Retry prompt injection or refresh ChatGPT page state.'}

    clicked = _click_send(client, req, target_id)
    debug[f'{prefix}clicked' if prefix else 'clicked'] = clicked
    if not clicked or not clicked.get('ok'):
        return None, {'errorCode': 'ERR_SUBMISSION_FAILED', 'error': f'Send click failed: {clicked}', 'nextStep': 'Verify send button enabled state and DOM label.'}

    submit_started = time.time()
    conversation_checks = []
    submit_polls = []
    fallback_done = False
    deadline = submit_started + max(10, req.submit_timeout_seconds)
    while time.time() < deadline:
        _wait(client, req.profile, target_id, 2500)
        submit_state = _submission_state(client, req, target_id)
        if not isinstance(submit_state, dict):
            submit_state = {'ok': False, 'error': 'submission state was not an object'}
        snap = client.snapshot(target_id=target_id, profile=req.profile, max_chars=20000, include_urls=True)
        url = (snap or {}).get('url') if isinstance(snap, dict) else None
        now = time.time()
        elapsed = round(now - submit_started, 3)
        conversation_checks.append({'ts': int(now), 'url': url})
        submit_polls.append({
            'ts': int(now),
            'elapsedSeconds': elapsed,
            'url': url,
            'state': submit_state,
        })
        submit_polls = submit_polls[-20:]
        if isinstance(snap, dict) and snap.get('urls'):
            debug[f'{prefix}snapshotUrls' if prefix else 'snapshotUrls'] = snap.get('urls')
        if url and '/c/' in url:
            debug[f'{prefix}snapshotUrl' if prefix else 'snapshotUrl'] = url
            debug[f'{prefix}conversationChecks' if prefix else 'conversationChecks'] = conversation_checks[-8:]
            debug[f'{prefix}submitPolls' if prefix else 'submitPolls'] = submit_polls
            return url, None
        if (
            not fallback_done
            and elapsed >= req.submit_retry_after_seconds
            and url
            and '/c/' not in url
            and submit_state.get('hasEditor')
            and int(submit_state.get('editorTextLength') or 0) > 0
        ):
            fallback = _fallback_submit(client, req, target_id)
            debug[f'{prefix}submitFallback' if prefix else 'submitFallback'] = {
                'elapsedSeconds': elapsed,
                'state': submit_state,
                'result': fallback,
            }
            fallback_done = True
    debug[f'{prefix}conversationChecks' if prefix else 'conversationChecks'] = conversation_checks[-8:]
    debug[f'{prefix}submitPolls' if prefix else 'submitPolls'] = submit_polls
    return None, {'errorCode': 'ERR_NO_CONVERSATION_URL', 'error': 'Submission did not reach a ChatGPT conversation URL.', 'nextStep': 'Check whether send succeeded but page remained on homepage.'}


def execute_state_machine(req: Request) -> Result:
    result = Result(ok=False, mode=req.mode, prompt=req.prompt, wrapped_prompt=wrap_prompt(req))
    client = BrowserClient(
        base_url=req.browser_base_url,
        cdp_url=req.cdp_url,
        token=req.browser_token,
        password=req.browser_password,
    )
    result.browserProfile = req.profile
    debug: dict[str, Any] = {
        'browserTransport': client.transport,
        'cdpUrl': client.cdp_url,
        'browserProfile': req.profile,
        'tabLabel': req.tab_label,
    }
    result.debug = debug

    target_id: Optional[str] = None
    try:
        target_url = req.conversation_url or CHATGPT_URL
        target_id = _open_ready_tab(client, req, target_url, debug, result)
        debug['initialTargetId'] = target_id
        debug['reopenedTab'] = False

        page_state = _wait_for_page_ready_state(client, req, target_id, debug)
        debug['pageStateCheck'] = page_state
        result.pageState = (page_state or {}).get('state')
        result.authState = (page_state or {}).get('authState')
        result.pageBlockReason = (page_state or {}).get('reason')
        if not page_state or not page_state.get('ok'):
            result.error = f'Page state detection failed: {page_state}'
            result.errorCode = 'ERR_UNKNOWN_BLOCKED_STATE'
            result.nextStep = 'Inspect ChatGPT homepage state and detection logic.'
            return result
        if result.pageState in {'login_required', 'human_verification'}:
            result.notificationNeeded = True
            result.notificationStage = 'blocked-detected'
            result.notificationMessage = _build_block_notification(result.pageState, req.recovery_timeout_seconds)
            recovery = _wait_for_manual_recovery(client, req, target_id, result.pageState)
            debug['recoveryWait'] = recovery
            if recovery.get('ok') and recovery.get('recovered'):
                result.recoveredFromBlock = True
                page_state = recovery.get('state') or page_state
                debug['pageStateCheckAfterRecovery'] = page_state
                result.pageState = (page_state or {}).get('state')
                result.authState = (page_state or {}).get('authState')
                result.pageBlockReason = (page_state or {}).get('reason')
                if recovery.get('notificationMessage'):
                    debug['recoveryNotification'] = {
                        'stage': recovery.get('notificationStage'),
                        'message': recovery.get('notificationMessage'),
                    }
            else:
                result.notificationNeeded = bool(recovery.get('notificationNeeded', True))
                result.notificationStage = recovery.get('notificationStage') or 'blocked-timeout'
                result.notificationMessage = recovery.get('notificationMessage') or result.notificationMessage
                if result.pageState == 'login_required':
                    result.error = 'ChatGPT requires login before prompt submission.'
                    result.errorCode = 'ERR_LOGIN_REQUIRED'
                    result.nextStep = 'Restore ChatGPT login state within the recovery window, then retry.'
                else:
                    result.error = 'ChatGPT is blocked by human verification.'
                    result.errorCode = 'ERR_HUMAN_VERIFICATION'
                    result.nextStep = 'Complete verification within the recovery window, then retry.'
                return result
        if result.pageState == 'blocked':
            result.error = 'ChatGPT page is loaded but currently blocked from normal use.'
            result.errorCode = 'ERR_UNKNOWN_BLOCKED_STATE'
            result.nextStep = 'Inspect page text/signals and retry after the block clears.'
            return result
        if result.pageState != 'ready':
            result.error = f'ChatGPT page is not in a ready state: {result.pageState}'
            result.errorCode = 'ERR_UNKNOWN_BLOCKED_STATE'
            result.nextStep = 'Inspect page debug info and update state detection rules if needed.'
            return result

        url, submit_error = _submit_prompt_and_get_conversation_url(client, req, target_id, debug)
        if submit_error:
            debug['cleanTabRetry'] = {'reason': submit_error.get('errorCode')}
            _safe_close_tab(client, req.profile, target_id, debug, key='closedBeforeRetry')
            target_id = _open_ready_tab(client, req, target_url, debug, result, prefix='retry')
            retry_page_state = _wait_for_page_ready_state(client, req, target_id, debug, prefix='retry')
            debug['retryPageStateCheck'] = retry_page_state
            if retry_page_state.get('state') == 'ready':
                url, submit_error = _submit_prompt_and_get_conversation_url(client, req, target_id, debug, prefix='retry')
            else:
                submit_error = {
                    'errorCode': 'ERR_UNKNOWN_BLOCKED_STATE',
                    'error': f"ChatGPT retry tab is not in a ready state: {retry_page_state.get('state')}",
                    'nextStep': 'Retry after the ChatGPT homepage finishes loading.',
                }
        if submit_error or not url:
            result.error = (submit_error or {}).get('error') or 'Submission did not reach a ChatGPT conversation URL.'
            result.errorCode = (submit_error or {}).get('errorCode') or 'ERR_NO_CONVERSATION_URL'
            result.nextStep = (submit_error or {}).get('nextStep') or 'Retry after resetting the ChatGPT tab.'
            return result

        deadline = time.time() + max(15, req.timeout_seconds)
        extracted: dict[str, Any] | None = None
        answer = ''
        stable_count = 0
        last_answer = None
        last_nonempty_extracted: dict[str, Any] | None = None
        final_good_answer = False
        final_is_streaming = False
        final_has_copy_button = False
        final_has_feedback = False
        final_stable_count = 0
        samples: list[dict[str, Any]] = []

        while time.time() < deadline:
            extracted = _extract_answer_and_sources(client, req, target_id)
            debug['extractedOk'] = extracted.get('ok') if isinstance(extracted, dict) else False
            if not extracted or not extracted.get('ok'):
                _wait(client, req.profile, target_id, 2000)
                continue

            answer = (extracted.get('text') or '').strip()
            is_streaming = bool(extracted.get('isStreaming'))
            has_copy_button = bool(extracted.get('hasCopyButton'))
            has_feedback = bool(extracted.get('hasPositiveFeedback')) or bool(extracted.get('hasNegativeFeedback'))
            
            cleaned_temp = clean_answer_text(answer)
            good_answer = bool(cleaned_temp and len(cleaned_temp) > 8)
            final_good_answer = good_answer
            final_is_streaming = is_streaming
            final_has_copy_button = has_copy_button
            final_has_feedback = has_feedback
            
            if good_answer:
                last_nonempty_extracted = extracted

            samples.append({
                'len': len(answer),
                'streaming': is_streaming,
                'copy': has_copy_button,
                'feedback': has_feedback,
                'copyMethod': extracted.get('copyMethod') if isinstance(extracted, dict) else None,
                'copyError': extracted.get('copyError') if isinstance(extracted, dict) else None,
                'preview': answer[:120]
            })
            samples = samples[-6:]

            if answer and answer == last_answer:
                stable_count += 1
            else:
                stable_count = 0
                last_answer = answer
            final_stable_count = stable_count

            if good_answer and has_copy_button and stable_count >= 1:
                break
            if good_answer and has_feedback and stable_count >= 1:
                break
            if good_answer and (not is_streaming) and stable_count >= 2:
                break

            _wait(client, req.profile, target_id, 2500)

        debug['samples'] = samples
        extracted = last_nonempty_extracted or extracted
        if not extracted or not extracted.get('ok'):
            result.error = f'Answer extraction failed: {extracted}'
            result.errorCode = 'ERR_EXTRACTION_FAILED'
            result.conversationUrl = url
            result.nextStep = 'Inspect extraction diagnostics and update ChatGPT assistant message selectors for the current Web UI.'
            return result

        answer = clean_answer_text((extracted.get('text') or '').strip())
        sources = [Source(**item) for item in extracted.get('links') or [] if item.get('href')]
        sources = dedupe_sources(sources)

        result.ok = True
        result.answer = answer
        result.conversationUrl = extracted.get('url') or url
        result.title = extracted.get('title') or req.title
        result.sources = sources if req.require_sources else []
        if extracted.get('usedClipboard'):
            result.extractionMode = 'copy'
        elif extracted.get('copyMethod') == 'dom-fallback':
            result.extractionMode = 'dom-markdown'
        else:
            result.extractionMode = 'innerText'
        result.usedClipboard = bool(extracted.get('usedClipboard'))
        result.copyInterceptWorked = bool(extracted.get('copyInterceptWorked'))
        completion_signal = final_has_copy_button or final_has_feedback or final_stable_count >= 2
        result.partial = (
            final_is_streaming
            or not final_good_answer
            or not completion_signal
        )
        if result.partial:
            result.nextStep = 'Answer may still be streaming or extraction may be incomplete.'
        return result
    except Exception as e:
        msg = str(e)
        result.error = msg
        if _is_cdp_transport_error(msg):
            result.errorCode = 'ERR_BROWSER_CDP_UNAVAILABLE'
            result.nextStep = 'Check `openclaw browser --browser-profile openclaw status` and `curl http://127.0.0.1:18800/json/version`, then retry.'
        elif 'ACT_TARGET_ID_MISMATCH' in msg or 'action targetId must match request targetId' in msg:
            result.errorCode = 'ERR_BROWSER_TARGET_MISMATCH'
            result.nextStep = 'Use the concrete OpenClaw CDP targetId; inspect openedTab.targetId and tabs output.'
        elif 'tab not found' in msg.lower():
            result.errorCode = 'ERR_TAB_NOT_FOUND'
            result.nextStep = 'Retry the run; the browser tab was closed or invalidated during execution.'
        elif _is_transient_browser_open_error(msg):
            result.errorCode = 'ERR_BROWSER_CDP_UNAVAILABLE'
            result.nextStep = 'OpenClaw managed Chrome/CDP was not available; wait for the browser cooldown to clear or restart OpenClaw/Chrome, then retry.'
        elif not result.errorCode:
            result.errorCode = 'ERR_UNKNOWN_BLOCKED_STATE'
            result.nextStep = 'Inspect local OpenClaw CDP service and ChatGPT DOM state.'
        return result
    finally:
        if target_id and not debug.get('closedTab'):
            _safe_close_tab(client, req.profile, target_id, debug)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='chatgpt-chat runner')
    p.add_argument('--prompt', required=True)
    p.add_argument('--mode', default='fetch-with-sources', choices=['fetch', 'fetch-with-sources', 'search', 'report'])
    p.add_argument('--title')
    p.add_argument('--conversation-url')
    p.add_argument('--save-report', action='store_true')
    p.add_argument('--report-path')
    p.add_argument('--profile', default=DEFAULT_PROFILE)
    p.add_argument('--tab-label', default='chatgpt-monitor')
    p.add_argument('--cdp-url')
    p.add_argument('--browser-base-url', help='Deprecated alias for --cdp-url')
    p.add_argument('--browser-token', help='Deprecated no-op for old Browser HTTP transport')
    p.add_argument('--browser-password', help='Deprecated no-op for old Browser HTTP transport')
    p.add_argument('--timeout-seconds', type=int, default=45)
    p.add_argument('--page-ready-timeout-seconds', type=int, default=180)
    p.add_argument('--submit-timeout-seconds', type=int, default=60)
    p.add_argument('--submit-retry-after-seconds', type=float, default=10.0)
    p.add_argument('--recovery-timeout-seconds', type=int, default=180)
    p.add_argument('--recovery-poll-ms', type=int, default=3000)
    p.add_argument('--stdin-json', action='store_true', help='Read full request JSON from stdin instead of flags')
    return p.parse_args()


def build_request(args: argparse.Namespace) -> Request:
    if args.stdin_json:
        data = json.load(sys.stdin)
        return Request(**data)
    return Request(
        prompt=args.prompt,
        mode=args.mode,
        require_sources=args.mode in {'fetch-with-sources', 'search', 'report'},
        save_report=args.save_report or args.mode == 'report',
        report_path=args.report_path,
        title=args.title,
        conversation_url=args.conversation_url,
        timeout_seconds=args.timeout_seconds,
        page_ready_timeout_seconds=args.page_ready_timeout_seconds,
        submit_timeout_seconds=args.submit_timeout_seconds,
        submit_retry_after_seconds=args.submit_retry_after_seconds,
        recovery_timeout_seconds=args.recovery_timeout_seconds,
        recovery_poll_ms=args.recovery_poll_ms,
        profile=args.profile,
        tab_label=args.tab_label,
        browser_base_url=args.browser_base_url,
        cdp_url=args.cdp_url,
        browser_token=args.browser_token,
        browser_password=args.browser_password,
    )


def main() -> int:
    args = parse_args()
    req = build_request(args)
    result = execute_state_machine(req)
    if result.ok and req.save_report:
        result.reportPath = save_report(result, req.report_path)
    payload = asdict(result)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
