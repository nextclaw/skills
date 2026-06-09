#!/usr/bin/env python3
"""gemini-chat runner.

Deterministic Gemini Web automation through OpenClaw 2026.6 CDP transport.
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
from typing import Any, Optional
from websockets.sync.client import connect as websocket_connect

GEMINI_URL = 'https://gemini.google.com/'
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
    save_report: bool = False
    report_path: Optional[str] = None
    title: Optional[str] = None
    conversation_url: Optional[str] = None
    timeout_seconds: int = 45
    recovery_timeout_seconds: int = 120
    recovery_poll_ms: int = 3000
    profile: str = DEFAULT_PROFILE
    tab_label: str = 'gemini-monitor'
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
    thoughtLabels: list[str] = field(default_factory=list)
    thinking: Optional[str] = None
    conversationUrl: Optional[str] = None
    title: Optional[str] = None
    sources: list[Source] = field(default_factory=list)
    reportPath: Optional[str] = None
    error: Optional[str] = None
    errorCode: Optional[str] = None
    pageState: Optional[str] = None
    authState: Optional[str] = None
    pageBlockReason: Optional[str] = None
    recoveredFromBlock: bool = False
    notificationNeeded: bool = False
    notificationStage: Optional[str] = None
    notificationMessage: Optional[str] = None
    nextStep: Optional[str] = None
    partial: bool = False
    extractionMode: Optional[str] = None
    usedClipboard: bool = False
    copyInterceptWorked: bool = False
    browserProfile: Optional[str] = None
    browserTarget: Optional[str] = None
    debug: dict[str, Any] = field(default_factory=dict)


class BrowserClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        cdp_url: Optional[str] = None,
        token: Optional[str] = None,
        password: Optional[str] = None,
    ):
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


def normalize_source_text(text: str, href: str) -> str:
    text = re.sub(r'\s+', ' ', (text or '').strip())
    if text and text.lower() not in {'source', 'sources', 'link', 'open link'}:
        return text
    parsed = urllib.parse.urlparse(href)
    host = parsed.netloc.replace('www.', '')
    return host or href


def is_usable_source_href(href: str) -> bool:
    href = (href or '').strip()
    if not href or href.startswith('#'):
        return False
    parsed = urllib.parse.urlparse(href)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if scheme not in {'http', 'https'}:
        return False
    if host == 'accounts.google.com':
        return False
    if 'signin' in path or 'login' in path:
        return False
    return True


def dedupe_sources(sources: list[Source]) -> list[Source]:
    seen: set[str] = set()
    out: list[Source] = []
    for src in sources:
        href = (src.href or '').strip()
        if not is_usable_source_href(href) or href in seen:
            continue
        seen.add(href)
        out.append(Source(text=normalize_source_text(src.text, href), href=href))
    return out


def sources_from_link_items(items: list[dict[str, Any]] | None) -> list[Source]:
    sources: list[Source] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        sources.append(Source(text=str(item.get('text') or ''), href=str(item.get('href') or '')))
    return dedupe_sources(sources)


_slug_re = re.compile(r'[^a-zA-Z0-9\u4e00-\u9fff]+')


def slugify(text: str, limit: int = 48) -> str:
    text = _slug_re.sub('-', text).strip('-').lower()
    text = re.sub(r'-+', '-', text)
    return text[:limit] or 'gemini-chat-report'


def render_report(result: Result) -> str:
    lines = []
    title = result.title or 'Gemini Chat Report'
    lines.append(f'# {title}')
    lines.append('')
    lines.append('> Generated by `gemini-chat` local skill')
    if result.conversationUrl:
        lines.append(f'> Conversation: `{result.conversationUrl}`')
    lines.append('')
    lines.append('## Prompt')
    lines.append('')
    lines.append(result.prompt)
    lines.append('')
    lines.append('## Answer')
    lines.append('')
    lines.append(result.answer.strip() or '_No answer captured._')
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


def _detect_page_state(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    fn = r"""() => {
      const text = (document.body?.innerText || '').trim();
      const href = location.href;
      const title = (document.title || '').trim();
      const editor = document.querySelector('.ql-editor, rich-textarea .ql-editor, div[contenteditable="true"], textarea');
      const loginSignals = ['Sign in', '登录', 'Continue with Google', '使用 Google 帐号继续'];
      const verificationSignals = ['Verify', 'robot', '验证', '安全检查', 'Just a moment'];
      const blockedSignals = ['Something went wrong', '暂时无法使用', 'Try again later', 'Access denied'];
      const loginMatched = loginSignals.filter(v => text.includes(v)).slice(0, 5);
      const verificationMatched = verificationSignals.filter(v => text.includes(v)).slice(0, 5);
      const blockedMatched = blockedSignals.filter(v => text.includes(v)).slice(0, 5);
      const hasLogin = loginMatched.length > 0;
      const hasVerification = verificationMatched.length > 0;
      const hasBlocked = blockedMatched.length > 0;
      let state = 'unknown';
      if (editor) state = 'ready';
      else if (hasVerification) state = 'human_verification';
      else if (hasLogin || href.includes('accounts.google.com') || href.includes('signin')) state = 'login_required';
      else if (hasBlocked) state = 'blocked';
      const authState = state === 'ready'
        ? 'authenticated-or-usable'
        : hasLogin ? 'guest-or-login-suggested' : 'authenticated-or-unknown';
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
        hasEditor: !!editor,
        loginMatched,
        verificationMatched,
        blockedMatched,
        textPreview: text.slice(0, 800)
      };
    }"""
    return _evaluate(client, req.profile, target_id, fn)


def _ensure_prompt_injected(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    prompt_json = json.dumps(wrap_prompt(req), ensure_ascii=False)
    fn = f"""() => {{
      const isVisible = (el) => {{
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      }};
      const pickEditor = () => {{
        const selectors = [
          '.ql-editor',
          'rich-textarea .ql-editor',
          'div[contenteditable="true"]',
          'textarea',
        ];
        const all = selectors.flatMap(sel => [...document.querySelectorAll(sel)]);
        const visible = all.filter(isVisible);
        const preferred = visible.find(el => el.isContentEditable) || visible[0] || all.find(el => el.isContentEditable) || all[0] || null;
        return preferred;
      }};
      const el = pickEditor();
      if (!el) return {{ok:false, reason:'no editor', method:null}};
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
          const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
          if (setter) setter.call(el, text);
          else el.value = text;
          el.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: text, inputType: 'insertText' }}));
          el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
      }} catch (err) {{
        if (el.isContentEditable) {{
          el.textContent = text;
          el.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: text, inputType: 'insertText' }}));
        }} else {{
          const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
          if (setter) setter.call(el, text);
          else el.value = text;
          el.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: text, inputType: 'insertText' }}));
          el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
        return {{ok:true, method:'inject-fallback', warning:String(err), tag: el.tagName, cls: el.className}};
      }}
      return {{ok:true, method: el.isContentEditable ? 'contenteditable-chunked' : 'value-set', tag: el.tagName, cls: el.className}};
    }}"""
    return _evaluate(client, req.profile, target_id, fn)


def _submit_prompt(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    fn = r"""() => {
      const isVisible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const pickEditor = () => {
        const selectors = ['.ql-editor', 'rich-textarea .ql-editor', 'div[contenteditable="true"]', 'textarea'];
        const all = selectors.flatMap(sel => [...document.querySelectorAll(sel)]);
        const visible = all.filter(isVisible);
        return visible.find(el => el.isContentEditable) || visible[0] || all.find(el => el.isContentEditable) || all[0] || null;
      };
      const editor = pickEditor();
      if (!editor) return {ok:false, reason:'no editor'};

      const editorText = () => {
        if (!editor) return '';
        if (editor.isContentEditable) return (editor.innerText || editor.textContent || '').trim();
        return String(editor.value || '').trim();
      };

      const beforeText = editorText();
      const sendSelectors = [
        'button.send-button',
        'button[data-test-id="send-button"]',
        'button[data-testid="send-button"]',
        'button[data-test-id*="send" i]',
        'button[data-testid*="send" i]',
        '.send-button-container button',
        'button[aria-label*="发送" i]',
        'button[aria-label*="提交" i]',
        'button[aria-label*="Send" i]',
        'button[aria-label*="Submit" i]',
        'button:has(mat-icon[fonticon="send"])',
        'button:has(mat-icon[data-mat-icon-name="send"])',
        'button:has(mat-icon[fonticon="send_arrow"])',
      ];

      let send = null;
      for (const sel of sendSelectors) {
        let btn = null;
        try {
          btn = [...document.querySelectorAll(sel)].find(isVisible) || null;
        } catch {
          btn = null;
        }
        const disabled = btn && (btn.disabled || btn.getAttribute('aria-disabled') === 'true' || btn.classList.contains('mat-mdc-button-disabled'));
        if (btn && !disabled) {
          send = btn;
          break;
        }
      }
      if (!send) {
        const buttons = [...document.querySelectorAll('button,[role="button"]')];
        send = buttons.find(b => {
          const label = ((b.getAttribute('aria-label') || b.innerText || b.textContent || '') + ' ' + (b.getAttribute('data-test-id') || '') + ' ' + (b.getAttribute('data-testid') || '')).toLowerCase();
          const icon = b.querySelector('mat-icon[fonticon*="send"], mat-icon[data-mat-icon-name*="send"], [fonticon*="send"]');
          const disabled = b.disabled || b.getAttribute('aria-disabled') === 'true' || b.classList.contains('mat-mdc-button-disabled');
          return isVisible(b) && !disabled && (label.includes('发送') || label.includes('提交') || label.includes('send') || label.includes('submit') || !!icon);
        }) || null;
      }

      if (send && !send.disabled && send.getAttribute('aria-disabled') !== 'true') {
        send.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, pointerId:1, pointerType:'mouse', isPrimary:true}));
        send.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, button:0}));
        send.dispatchEvent(new PointerEvent('pointerup', {bubbles:true, pointerId:1, pointerType:'mouse', isPrimary:true}));
        send.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, button:0}));
        send.click();
        return {
          ok:true,
          method:'send-button',
          label: send.getAttribute('aria-label') || send.innerText || send.textContent || '',
          beforeText,
          hadSpecificSelector: true,
        };
      }

      editor.focus();
      editor.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', which:13, keyCode:13, bubbles:true}));
      editor.dispatchEvent(new KeyboardEvent('keypress', {key:'Enter', code:'Enter', which:13, keyCode:13, bubbles:true}));
      editor.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter', code:'Enter', which:13, keyCode:13, bubbles:true}));
      return {ok:true, method:'enter-fallback', beforeText, hadSpecificSelector:false};
    }"""
    return _evaluate(client, req.profile, target_id, fn)


def _confirm_submission(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    deadline = time.time() + 12
    checks: list[dict[str, Any]] = []
    while time.time() < deadline:
        state = _evaluate(client, req.profile, target_id, r"""() => {
          const editor = document.querySelector('.ql-editor, rich-textarea .ql-editor, div[contenteditable="true"], textarea');
          const editorText = (() => {
            if (!editor) return null;
            if (editor.isContentEditable) return (editor.innerText || editor.textContent || '').trim();
            return String(editor.value || '').trim();
          })();
          const buttons = [...document.querySelectorAll('button,[role="button"]')];
          const stop = buttons.find(b => {
            const label = (b.getAttribute('aria-label') || b.innerText || b.textContent || '').trim();
            return label === '停止回答' || label === 'Stop responding';
          });
          const copy = buttons.find(b => {
            const label = ((b.getAttribute('aria-label') || '') + ' ' + (b.innerText || b.textContent || '')).toLowerCase();
            return label.includes('copy') || label.includes('复制') || !!b.querySelector('mat-icon[fonticon="content_copy"], .embedded-copy-icon, [fonticon="content_copy"]');
          });
          const hasGeminiHeading = [...document.querySelectorAll('h1,h2,h3,h4')].some(h => {
            const t = (h.innerText || h.textContent || '').trim();
            return t === 'Gemini 说' || t === 'Gemini said';
          });
          const bodyText = document.body?.innerText || '';
          const hasAssistantText = /Gemini 说|Gemini said|停止回答|Stop responding/.test(bodyText);
          return {
            ok: true,
            editorText,
            hasStop: !!stop,
            hasCopy: !!copy,
            hasGeminiHeading,
            hasAssistantText,
            href: location.href,
          };
        }""")
        checks.append({
            'ts': int(time.time()),
            'editorTextLen': len((state or {}).get('editorText') or ''),
            'hasStop': bool((state or {}).get('hasStop')),
            'hasCopy': bool((state or {}).get('hasCopy')),
            'hasGeminiHeading': bool((state or {}).get('hasGeminiHeading')),
            'hasAssistantText': bool((state or {}).get('hasAssistantText')),
        })
        checks = checks[-6:]
        if state and state.get('ok'):
            editor_text = (state.get('editorText') or '').strip()
            if state.get('hasStop') or state.get('hasCopy') or state.get('hasGeminiHeading') or state.get('hasAssistantText') or not editor_text:
                return {'ok': True, 'confirmed': True, 'state': state, 'checks': checks}
        _wait(client, req.profile, target_id, 1200)
    return {'ok': False, 'confirmed': False, 'checks': checks}


def _extract_answer(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    prompt_json = json.dumps(req.prompt, ensure_ascii=False)
    fn = rf"""async () => {{
      const stopButton = [...document.querySelectorAll('button,[role="button"]')].find(b => {{
        const label = (b.getAttribute('aria-label') || b.innerText || b.textContent || '').trim();
        return label === '停止回答' || label === 'Stop responding';
      }});
      const isStreaming = !!stopButton;
      const prompt = {prompt_json};

      const cleanText = (raw) => {{
        let text = String(raw || '').replace(/\u00a0/g, ' ');
        text = text.replace(/^显示思路\s*/gm, '');
        text = text.replace(/^Show thinking\s*/gim, '');
        text = text.replace(/^Gemini 说\s*/gm, '');
        text = text.replace(/^Gemini said\s*/gim, '');
        text = text.replace(/^立即回答\s*/gm, '');
        text = text.replace(/^Answer now\s*/gim, '');
        text = text.replace(/^Gemini 是一款 AI 工具，其回答未必正确无误。\s*/gm, '');
        text = text.replace(/^Gemini can make mistakes, so double-check it\.?\s*/gim, '');
        if (text.startsWith(prompt)) text = text.slice(prompt.length).trim();
        text = text.replace(/^\s+|\s+$/g, '');
        return text;
      }};

      const isThoughtLabel = (line) => {{
        const s = String(line || '').trim();
        if (!s || s.length > 80) return false;
        if (!/^[A-Z][A-Za-z\s\-:()&/,.'"]+$/.test(s)) return false;
        const words = s.split(/\s+/).filter(Boolean);
        if (words.length < 2 || words.length > 8) return false;
        return !/[。！？；：，、]|\d{{2,}}/.test(s);
      }};

      const splitThinking = (text) => {{
        const lines = text.split(/\r?\n/).map(x => x.trim());
        const thoughtLabels = [];
        while (lines.length && isThoughtLabel(lines[0])) {{
          thoughtLabels.push(lines.shift());
        }}
        return {{
          thoughtLabels,
          thinking: thoughtLabels.length ? thoughtLabels.join(' | ') : null,
          answer: lines.join('\n').trim(),
        }};
      }};

      const findLatestCopyButton = () => {{
        const buttons = [...document.querySelectorAll('button,[role="button"]')];
        const candidates = buttons.filter(btn => {{
          const label = ((btn.getAttribute('aria-label') || '') + ' ' + (btn.innerText || btn.textContent || '') + ' ' + (btn.getAttribute('data-test-id') || '')).toLowerCase();
          if (label.includes('copy') || label.includes('复制')) return true;
          return !!btn.querySelector('mat-icon[fonticon="content_copy"], .embedded-copy-icon, [fonticon="content_copy"]');
        }});
        return candidates.at(-1) || null;
      }};

      let copyText = '';
      let copyButtonMeta = null;
      let copyClicked = false;
      let clipboardWorked = false;
      try {{
        const copyBtn = findLatestCopyButton();
        if (copyBtn && !copyBtn.disabled) {{
          copyButtonMeta = {{
            aria: copyBtn.getAttribute('aria-label') || null,
            text: (copyBtn.innerText || copyBtn.textContent || '').trim() || null,
            cls: copyBtn.className || null,
          }};
          copyClicked = true;
        }}
      }} catch (err) {{
        copyButtonMeta = {{ ...(copyButtonMeta || {{}}), error: String(err) }};
      }}

      let sourceText = copyText;
      let answerBlock = null;
      let extractionMode = copyText ? 'copy' : (copyClicked ? 'copy-probed-dom-fallback' : 'dom-text');
      if (!sourceText) {{
        const headings = [...document.querySelectorAll('h1,h2,h3,h4')];
        const geminiHeading = headings.filter(h => {{
          const t = (h.innerText || h.textContent || '').trim();
          return t === 'Gemini 说' || t === 'Gemini said';
        }}).pop();

        let block = null;
        if (geminiHeading) {{
          block = geminiHeading.parentElement;
          while (block && block.innerText && !block.innerText.includes('Gemini 说') && !block.innerText.includes('Gemini said')) {{
            block = block.parentElement;
          }}
          if (!block) block = geminiHeading.parentElement || geminiHeading;
        }} else {{
          const candidates = [...document.querySelectorAll('message-content, .model-response-text, .response-content, .markdown, .model-response, .conversation-container *')]
            .map(el => ({{el, text:(el.innerText || el.textContent || '').trim()}}))
            .filter(x => x.text && x.text.length > 80)
            .filter(x => !x.text.includes(prompt))
            .filter(x => !/^Gemini\s*$/i.test(x.text))
            .filter(x => !/^(与 Gemini 对话|需要我为你做些什么？)$/m.test(x.text));
          block = candidates.at(-1)?.el || null;
        }}

        if (!block) return {{ok:false, reason:'no assistant block yet', extractionMode, copyClicked, copyButtonMeta}};
        answerBlock = block;
        sourceText = (block.innerText || block.textContent || '').trim();
      }}

      const isUsableHref = (href) => {{
        if (!href) return false;
        try {{
          const url = new URL(href, location.href);
          if (!['http:', 'https:'].includes(url.protocol)) return false;
          if (url.hash && !url.pathname.replace(/\//g, '') && !url.search) return false;
          const host = url.hostname.toLowerCase();
          const path = url.pathname.toLowerCase();
          if (host === 'accounts.google.com') return false;
          if (path.includes('signin') || path.includes('login')) return false;
          return true;
        }} catch (err) {{
          return false;
        }}
      }};
      const links = answerBlock ? [...answerBlock.querySelectorAll('a[href]')]
        .map(a => ({{
          text: (a.innerText || a.textContent || a.getAttribute('aria-label') || '').trim(),
          href: a.href || a.getAttribute('href') || '',
        }}))
        .filter(link => isUsableHref(link.href))
        : [];

      const cleaned = cleanText(sourceText);
      const split = splitThinking(cleaned);

      return {{
        ok: !!split.answer,
        text: split.answer,
        thoughtLabels: split.thoughtLabels,
        thinking: split.thinking,
        isStreaming,
        url: location.href,
        title: (document.title || '').trim() || null,
        extractionMode,
        usedClipboard: clipboardWorked,
        copyInterceptWorked: clipboardWorked,
        copyClicked,
        copyButtonMeta,
        links,
        linkCount: links.length,
        rawPreview: sourceText.slice(0, 400),
      }};
    }}"""
    return _evaluate(client, req.profile, target_id, fn)


def _build_block_notification(state: str, timeout_seconds: int) -> str:
    if state == 'login_required':
        return f'Gemini Web 当前需要登录或继续认证。请在浏览器中处理；runner 将在接下来约 {timeout_seconds} 秒内持续等待并自动继续。'
    if state == 'human_verification':
        return f'Gemini Web 当前触发了验证或安全检查。请在浏览器中处理；runner 将在接下来约 {timeout_seconds} 秒内持续等待并自动继续。'
    return f'Gemini Web 当前处于阻断状态。请在浏览器中检查；runner 将在接下来约 {timeout_seconds} 秒内持续等待并自动继续。'


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
                'notificationMessage': 'Gemini Web 已恢复到可用状态，runner 将继续执行当前任务。',
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


def _open_ready_tab(client: BrowserClient, req: Request, target_url: str, debug: dict[str, Any], result: Result, *, prefix: str = '') -> str:
    opened = client.open_tab(target_url, req.profile, req.tab_label)
    target_id = _target_from_opened(opened, req.tab_label)
    result.browserTarget = target_id
    debug[f'{prefix}openedTab' if prefix else 'openedTab'] = opened
    debug[f'{prefix}targetId' if prefix else 'targetId'] = target_id
    _wait_until_tab_ready(client, req.profile, target_id, debug=debug)
    _wait(client, req.profile, target_id, 2500)
    return target_id


def _submit_and_confirm(client: BrowserClient, req: Request, target_id: str, debug: dict[str, Any], *, prefix: str = '') -> dict[str, Any] | None:
    injected = _ensure_prompt_injected(client, req, target_id)
    debug[f'{prefix}injected' if prefix else 'injected'] = injected
    if not injected or not injected.get('ok'):
        return {'errorCode': 'ERR_NO_EDITOR', 'error': f'Prompt injection failed: {injected}', 'nextStep': 'Re-check Gemini editor selectors.'}

    submitted = _submit_prompt(client, req, target_id)
    debug[f'{prefix}submitted' if prefix else 'submitted'] = submitted
    if not submitted or not submitted.get('ok'):
        return {'errorCode': 'ERR_SUBMISSION_FAILED', 'error': f'Prompt submission failed: {submitted}', 'nextStep': 'Verify editor state and Gemini submit path.'}

    submission_confirm = _confirm_submission(client, req, target_id)
    debug[f'{prefix}submissionConfirm' if prefix else 'submissionConfirm'] = submission_confirm
    if not submission_confirm.get('ok') or not submission_confirm.get('confirmed'):
        return {
            'errorCode': 'ERR_SUBMISSION_FAILED',
            'error': f'Prompt submission did not produce Gemini response signals: {submission_confirm}',
            'nextStep': 'Prefer real Gemini send-button submission; editor input alone was not enough.',
        }
    return None


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
        target_url = req.conversation_url or GEMINI_URL
        target_id = _open_ready_tab(client, req, target_url, debug, result)
        debug['initialTargetId'] = target_id
        debug['reopenedTab'] = False

        page_state = _detect_page_state(client, req, target_id)
        debug['pageStateCheck'] = page_state
        result.pageState = (page_state or {}).get('state')
        result.authState = (page_state or {}).get('authState')
        result.pageBlockReason = (page_state or {}).get('reason')
        if not page_state or not page_state.get('ok'):
            result.error = f'Page state detection failed: {page_state}'
            result.errorCode = 'ERR_UNKNOWN_BLOCKED_STATE'
            result.nextStep = 'Inspect Gemini page state and detection logic.'
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
            else:
                result.notificationNeeded = bool(recovery.get('notificationNeeded', True))
                result.notificationStage = recovery.get('notificationStage') or 'blocked-timeout'
                result.notificationMessage = recovery.get('notificationMessage') or result.notificationMessage
                result.error = 'Gemini Web is blocked by login or verification requirements.'
                result.errorCode = 'ERR_LOGIN_REQUIRED' if result.pageState == 'login_required' else 'ERR_HUMAN_VERIFICATION'
                result.nextStep = 'Complete the required Gemini login/verification flow within the recovery window, then retry.'
                return result

        if result.pageState == 'blocked':
            result.error = 'Gemini Web page is loaded but currently blocked from normal use.'
            result.errorCode = 'ERR_UNKNOWN_BLOCKED_STATE'
            result.nextStep = 'Inspect page text/signals and retry after the block clears.'
            return result
        if result.pageState != 'ready':
            result.error = f'Gemini page is not in a ready state: {result.pageState}'
            result.errorCode = 'ERR_UNKNOWN_BLOCKED_STATE'
            result.nextStep = 'Inspect Gemini page debug info and update detection rules if needed.'
            return result

        submit_error = _submit_and_confirm(client, req, target_id, debug)
        if submit_error:
            debug['cleanTabRetry'] = {'reason': submit_error.get('errorCode')}
            _safe_close_tab(client, req.profile, target_id, debug, key='closedBeforeRetry')
            target_id = _open_ready_tab(client, req, target_url, debug, result, prefix='retry')
            submit_error = _submit_and_confirm(client, req, target_id, debug, prefix='retry')
        if submit_error:
            result.error = submit_error.get('error')
            result.errorCode = submit_error.get('errorCode')
            result.nextStep = submit_error.get('nextStep')
            return result

        deadline = time.time() + max(15, req.timeout_seconds)
        extracted: dict[str, Any] | None = None
        answer = ''
        stable_count = 0
        last_answer = None
        last_nonempty: dict[str, Any] | None = None
        samples: list[dict[str, Any]] = []

        while time.time() < deadline:
            extracted = _extract_answer(client, req, target_id)
            debug['extractedOk'] = extracted.get('ok') if isinstance(extracted, dict) else False
            if not extracted or not extracted.get('ok'):
                _wait(client, req.profile, target_id, 2000)
                continue

            answer = (extracted.get('text') or '').strip()
            is_streaming = bool(extracted.get('isStreaming'))
            if answer and len(answer) > 12:
                last_nonempty = extracted

            samples.append({
                'len': len(answer),
                'streaming': is_streaming,
                'linkCount': len(extracted.get('links') or []) if isinstance(extracted, dict) else 0,
                'preview': answer[:120],
            })
            samples = samples[-6:]

            if answer and answer == last_answer:
                stable_count += 1
            else:
                stable_count = 0
                last_answer = answer

            if answer and not is_streaming and stable_count >= 1:
                break
            if answer and stable_count >= 3:
                break
            _wait(client, req.profile, target_id, 2500)

        debug['samples'] = samples
        extracted = last_nonempty or extracted
        if not extracted or not extracted.get('ok'):
            result.error = f'Answer extraction failed: {extracted}'
            result.errorCode = 'ERR_EXTRACTION_FAILED'
            result.conversationUrl = (extracted or {}).get('url') if isinstance(extracted, dict) else None
            result.nextStep = 'Inspect Gemini response selectors and current page structure.'
            return result

        result.ok = True
        result.answer = (extracted.get('text') or '').strip()
        result.thoughtLabels = list(extracted.get('thoughtLabels') or [])
        result.thinking = extracted.get('thinking')
        result.conversationUrl = extracted.get('url')
        result.title = extracted.get('title') or req.title
        result.sources = sources_from_link_items(extracted.get('links') or [])
        result.extractionMode = extracted.get('extractionMode')
        result.usedClipboard = bool(extracted.get('usedClipboard'))
        result.copyInterceptWorked = bool(extracted.get('copyInterceptWorked'))
        result.partial = bool(extracted.get('isStreaming')) or not result.answer
        if result.partial:
            result.nextStep = 'Gemini answer may still be streaming or incomplete.'

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
            result.nextStep = 'Retry the run; the Gemini browser tab was closed or invalidated during execution.'
        elif not result.errorCode:
            result.errorCode = 'ERR_UNKNOWN_BLOCKED_STATE'
            result.nextStep = 'Inspect local OpenClaw CDP service and Gemini DOM state.'
        return result
    finally:
        if target_id and not debug.get('closedTab'):
            _safe_close_tab(client, req.profile, target_id, debug)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='gemini-chat runner')
    p.add_argument('--prompt', required=True)
    p.add_argument('--mode', default='fetch-with-sources', choices=['fetch', 'fetch-with-sources', 'search', 'report'])
    p.add_argument('--title')
    p.add_argument('--conversation-url')
    p.add_argument('--save-report', action='store_true')
    p.add_argument('--report-path')
    p.add_argument('--profile', default=DEFAULT_PROFILE)
    p.add_argument('--tab-label', default='gemini-monitor')
    p.add_argument('--cdp-url')
    p.add_argument('--browser-base-url', help='Deprecated alias for --cdp-url')
    p.add_argument('--browser-token', help='Deprecated no-op for old Browser HTTP transport')
    p.add_argument('--browser-password', help='Deprecated no-op for old Browser HTTP transport')
    p.add_argument('--timeout-seconds', type=int, default=45)
    p.add_argument('--recovery-timeout-seconds', type=int, default=120)
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
        save_report=args.save_report,
        report_path=args.report_path,
        title=args.title,
        conversation_url=args.conversation_url,
        timeout_seconds=args.timeout_seconds,
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
