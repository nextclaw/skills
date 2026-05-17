#!/usr/bin/env python3
"""gemini-chat runner.

Deterministic Gemini Web automation via the local OpenClaw browser control service.
OpenClaw browser control is the primary automation surface; this runner is the
fixed workflow implementation for Gemini single-turn tasks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Optional

OPENCLAW_CONFIG = Path(os.environ.get('OPENCLAW_CONFIG', '~/.openclaw/openclaw.json')).expanduser()
GEMINI_URL = 'https://gemini.google.com/'
DEFAULT_PROFILE = 'openclaw'


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
    browser_transport: str = 'cli'


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
    def __init__(self, config_path: Path = OPENCLAW_CONFIG, transport: str = 'cli'):
        self.transport = (os.environ.get('OPENCLAW_BROWSER_TRANSPORT') or transport or 'cli').strip().lower()
        self.cli = os.environ.get('OPENCLAW_CLI') or shutil.which('openclaw') or 'openclaw'
        cfg = json.loads(config_path.read_text(encoding='utf-8'))
        gateway_port = int(((cfg.get('gateway') or {}).get('port')) or 18789)
        self.base_url = f'http://127.0.0.1:{gateway_port + 2}'
        auth = ((cfg.get('gateway') or {}).get('auth') or {})
        self.token = (auth.get('token') or '').strip()
        self.password = (auth.get('password') or '').strip()

    def _headers(self, json_body: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        elif self.password:
            headers['x-openclaw-password'] = self.password
        if json_body:
            headers['Content-Type'] = 'application/json'
        return headers

    def _parse_cli_output(self, raw: str) -> Any:
        text = (raw or '').strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        for marker in ('{', '['):
            idx = text.find(marker)
            if idx >= 0:
                try:
                    return json.loads(text[idx:])
                except json.JSONDecodeError:
                    pass
        parsed: dict[str, Any] = {'text': text}
        for line in text.splitlines():
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()
            if key and value and re.match(r'^[A-Za-z][A-Za-z0-9_-]*$', key):
                parsed[key] = value
        return parsed

    def _cli_request(self, profile: str, args: list[str], timeout: int = 20) -> Any:
        cmd = [self.cli, 'browser', '--browser-profile', profile, '--json', *args]
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
        except FileNotFoundError as e:
            raise RuntimeError('OpenClaw CLI is not available in PATH; set OPENCLAW_CLI or use --browser-transport http.') from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f'OpenClaw browser CLI timed out: {" ".join(cmd[:4] + args[:2])}') from e
        if proc.returncode != 0:
            payload = (proc.stderr or proc.stdout or '').strip()
            raise RuntimeError(f'OpenClaw browser CLI failed ({proc.returncode}): {payload}')
        return self._parse_cli_output(proc.stdout)

    def request(self, method: str, path: str, body: Optional[dict[str, Any]] = None, timeout: int = 20) -> Any:
        url = f'{self.base_url}{path}'
        data = None
        headers = self._headers(json_body=body is not None)
        if body is not None:
            data = json.dumps(body).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode('utf-8')
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            payload = e.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'Browser HTTP {e.code}: {payload}') from e
        except urllib.error.URLError as e:
            raise RuntimeError(f'Browser request failed: {e}') from e

    def open_tab(self, url: str, profile: str, label: Optional[str] = None) -> dict[str, Any]:
        if self.transport == 'cli':
            opened = self._cli_request(profile, ['open', url], timeout=30)
            if isinstance(opened, dict):
                if label and 'label' not in opened:
                    opened['label'] = label
                return opened
            return {'url': url, 'label': label, 'raw': opened}
        body: dict[str, Any] = {'url': url}
        if label:
            body['label'] = label
        try:
            return self.request('POST', f'/tabs/open?profile={urllib.parse.quote(profile)}', body, timeout=15)
        except RuntimeError as e:
            err = str(e)
            if label and ('label' in err.lower() or 'Browser HTTP 400' in err):
                return self.request('POST', f'/tabs/open?profile={urllib.parse.quote(profile)}', {'url': url}, timeout=15)
            raise

    def tabs(self, profile: str) -> list[dict[str, Any]]:
        if self.transport == 'cli':
            tabs = self._cli_request(profile, ['tabs'], timeout=15)
            if isinstance(tabs, list):
                return [tab for tab in tabs if isinstance(tab, dict)]
            if isinstance(tabs, dict):
                value = tabs.get('tabs') or tabs.get('items') or []
                return [tab for tab in value if isinstance(tab, dict)]
            return []
        tabs = self.request('GET', f'/tabs?profile={urllib.parse.quote(profile)}', timeout=10)
        return [tab for tab in (tabs or {}).get('tabs', []) if isinstance(tab, dict)]

    def snapshot(self, *, target_id: str, profile: str, max_chars: int = 12000, refs: str = 'aria', fmt: str = 'aria', include_urls: bool = True) -> dict[str, Any]:
        if self.transport == 'cli':
            if target_id:
                self._cli_request(profile, ['focus', target_id], timeout=10)
            args = ['snapshot', '--format', fmt]
            if include_urls:
                args.append('--urls')
            snap = self._cli_request(profile, args, timeout=30)
            if not isinstance(snap, dict):
                snap = {'text': snap}
            try:
                href = self.act(profile=profile, payload={'kind': 'evaluate', 'targetId': target_id, 'fn': '() => location.href'})
                if isinstance(href, dict):
                    snap['url'] = href.get('result') or href.get('value') or href.get('returnValue')
            except Exception as e:
                snap['urlError'] = str(e)
            return snap
        params = {
            'targetId': target_id,
            'maxChars': str(max_chars),
            'refs': refs,
            'format': fmt,
            'profile': profile,
        }
        if include_urls:
            params['urls'] = 'true'
        q = urllib.parse.urlencode(params)
        return self.request('GET', f'/snapshot?{q}', timeout=20)

    def act(self, *, profile: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.transport == 'cli':
            target_id = str(payload.get('targetId') or '')
            if target_id:
                self._cli_request(profile, ['focus', target_id], timeout=10)
            kind = payload.get('kind')
            if kind == 'wait':
                time.sleep(max(0, int(payload.get('timeMs') or 0)) / 1000)
                return {'ok': True}
            if kind == 'evaluate':
                out = self._cli_request(profile, ['evaluate', '--fn', str(payload.get('fn') or '')], timeout=30)
                if isinstance(out, dict):
                    return out
                return {'result': out}
            raise RuntimeError(f'Unsupported OpenClaw CLI browser action kind: {kind}')
        return self.request('POST', f'/act?profile={urllib.parse.quote(profile)}', payload, timeout=20)

    def close_tab(self, target_id: str, profile: str) -> None:
        if self.transport == 'cli':
            self._cli_request(profile, ['close', target_id], timeout=10)
            return
        self.request('DELETE', f'/tabs/{urllib.parse.quote(target_id)}?profile={urllib.parse.quote(profile)}', timeout=10)


def wrap_prompt(req: Request) -> str:
    if req.mode == 'search':
        return f'请使用网页搜索能力回答以下问题，并列出主要来源：{req.prompt}'
    if req.mode == 'report':
        return (
            '请使用网页搜索能力回答以下问题，并尽量结构化输出为简洁报告，'
            f'最后列出主要来源：{req.prompt}'
        )
    if req.mode == 'fetch-with-sources':
        return f'请回答以下问题，并列出主要来源：{req.prompt}'
    return req.prompt


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
    """Prefer OpenClaw's stable tab handles over volatile raw CDP target IDs."""
    for key in ('suggestedTargetId', 'tabId', 'targetId', 'id', 'label'):
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
      const authState = hasLogin ? 'guest-or-login-suggested' : 'authenticated-or-unknown';
      let state = 'unknown';
      if (editor) state = 'ready';
      else if (hasVerification) state = 'human_verification';
      else if (hasLogin || href.includes('accounts.google.com') || href.includes('signin')) state = 'login_required';
      else if (hasBlocked) state = 'blocked';
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
        return {{ok:true, method:'inject-fallback', warning:String(err), tag: el.tagName, cls: el.className}};
      }}
      return {{ok:true, method: el.isContentEditable ? 'contenteditable-chunked' : 'value-set', tag: el.tagName, cls: el.className}};
    }}"""
    return _evaluate(client, req.profile, target_id, fn)


def _submit_prompt(client: BrowserClient, req: Request, target_id: str) -> dict[str, Any]:
    fn = r"""() => {
      const editor = document.querySelector('.ql-editor, rich-textarea .ql-editor, div[contenteditable="true"], textarea');
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
        '.send-button-container button',
        'button[aria-label="发送"]',
        'button[aria-label="Send message"]',
        'button[aria-label="Send"]',
      ];

      let send = null;
      for (const sel of sendSelectors) {
        const btn = document.querySelector(sel);
        if (btn && !btn.disabled) {
          send = btn;
          break;
        }
      }
      if (!send) {
        const buttons = [...document.querySelectorAll('button,[role="button"]')];
        send = buttons.find(b => {
          const label = ((b.getAttribute('aria-label') || b.innerText || b.textContent || '') + ' ' + (b.getAttribute('data-test-id') || '')).toLowerCase();
          return !b.disabled && (label.includes('发送') || label.includes('send') || label.includes('submit'));
        }) || null;
      }

      if (send && !send.disabled) {
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
        sourceText = (block.innerText || block.textContent || '').trim();
      }}

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


def execute_state_machine(req: Request) -> Result:
    result = Result(ok=False, mode=req.mode, prompt=req.prompt, wrapped_prompt=wrap_prompt(req))
    client = BrowserClient(transport=req.browser_transport)
    result.browserProfile = req.profile
    debug: dict[str, Any] = {'baseUrl': client.base_url, 'browserTransport': client.transport, 'browserProfile': req.profile, 'tabLabel': req.tab_label}
    result.debug = debug

    try:
        target_url = req.conversation_url or GEMINI_URL
        opened = client.open_tab(target_url, req.profile, req.tab_label)
        target_id = _target_from_opened(opened, req.tab_label)
        result.browserTarget = target_id
        debug['openedTab'] = opened
        debug['initialTargetId'] = target_id
        debug['targetId'] = target_id
        debug['reopenedTab'] = False
        try:
            _wait_until_tab_ready(client, req.profile, target_id, debug=debug)
        except Exception as ready_err:
            if 'tab not found' in str(ready_err).lower():
                debug['initialReadyError'] = str(ready_err)
                debug['reopenedTab'] = True
                reopened = client.open_tab(target_url, req.profile, req.tab_label)
                target_id = _target_from_opened(reopened, req.tab_label)
                result.browserTarget = target_id
                debug['reopenedTabDetails'] = reopened
                debug['replacementTargetId'] = target_id
                debug['targetId'] = target_id
                _wait_until_tab_ready(client, req.profile, target_id, debug=debug)
            else:
                raise
        _wait(client, req.profile, target_id, 2500)

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

        injected = _ensure_prompt_injected(client, req, target_id)
        debug['injected'] = injected
        if not injected or not injected.get('ok'):
            result.error = f'Prompt injection failed: {injected}'
            result.errorCode = 'ERR_NO_EDITOR'
            result.nextStep = 'Re-check Gemini editor selectors.'
            return result

        submitted = _submit_prompt(client, req, target_id)
        debug['submitted'] = submitted
        if not submitted or not submitted.get('ok'):
            result.error = f'Prompt submission failed: {submitted}'
            result.errorCode = 'ERR_SUBMISSION_FAILED'
            result.nextStep = 'Verify editor state and Gemini submit path.'
            return result

        submission_confirm = _confirm_submission(client, req, target_id)
        debug['submissionConfirm'] = submission_confirm
        if not submission_confirm.get('ok') or not submission_confirm.get('confirmed'):
            result.error = f'Prompt submission did not produce Gemini response signals: {submission_confirm}'
            result.errorCode = 'ERR_SUBMISSION_FAILED'
            result.nextStep = 'Prefer real Gemini send-button submission; editor input alone was not enough.'
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
        result.extractionMode = extracted.get('extractionMode')
        result.usedClipboard = bool(extracted.get('usedClipboard'))
        result.copyInterceptWorked = bool(extracted.get('copyInterceptWorked'))
        result.partial = bool(extracted.get('isStreaming')) or not result.answer
        if result.partial:
            result.nextStep = 'Gemini answer may still be streaming or incomplete.'

        try:
            client.close_tab(target_id, req.profile)
            debug['closedTab'] = True
        except Exception as close_err:
            debug['closedTab'] = False
            debug['closeTabError'] = str(close_err)
        return result
    except Exception as e:
        msg = str(e)
        result.error = msg
        if 'tab not found' in msg.lower():
            result.errorCode = 'ERR_TAB_NOT_FOUND'
            result.nextStep = 'Retry the run; the Gemini browser tab was closed or invalidated during execution.'
        elif 'Browser HTTP 401' in msg or 'Unauthorized' in msg:
            result.errorCode = 'ERR_BROWSER_UNAUTHORIZED'
            result.nextStep = 'Use the default OpenClaw CLI browser transport, or provide the current Browser HTTP shared-secret before using --browser-transport http.'
        elif not result.errorCode:
            result.errorCode = 'ERR_UNKNOWN_BLOCKED_STATE'
            result.nextStep = 'Inspect local browser control service and Gemini DOM state.'
        return result


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
    p.add_argument('--browser-transport', default='cli', choices=['cli', 'http'])
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
        browser_transport=args.browser_transport,
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
