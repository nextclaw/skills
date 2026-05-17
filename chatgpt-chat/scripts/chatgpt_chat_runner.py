#!/usr/bin/env python3
"""chatgpt-chat runner.

Deterministic ChatGPT Web automation via the local OpenClaw browser control service.

This implementation uses the OpenClaw browser control HTTP surface directly instead
of tool calls, which makes it suitable as a local runner while staying aligned with
OpenClaw's browser/runtime/profile model.
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
from typing import Any, List, Optional

OPENCLAW_CONFIG = Path(os.environ.get('OPENCLAW_CONFIG', '~/.openclaw/openclaw.json')).expanduser()
CHATGPT_URL = 'https://chatgpt.com/'
DEFAULT_PROFILE = 'openclaw'


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
    recovery_timeout_seconds: int = 180
    recovery_poll_ms: int = 3000
    profile: str = DEFAULT_PROFILE
    tab_label: str = 'chatgpt-monitor'
    browser_transport: str = 'cli'


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
        return f"请使用网页搜索能力回答以下问题，并列出主要来源：{req.prompt}"
    if req.mode == 'report':
        return (
            '请使用网页搜索能力回答以下问题，并尽量结构化输出为简洁报告，'
            f'最后列出主要来源：{req.prompt}'
        )
    if req.mode == 'fetch-with-sources':
        return f"请回答以下问题，并列出主要来源：{req.prompt}"
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
      const authState = hasLogin ? 'guest-or-login-suggested' : 'authenticated-or-unknown';
      let state = 'unknown';
      if (textbox) state = 'ready';
      else if (hasVerification) state = 'human_verification';
      else if (hasLogin || href.includes('/auth') || href.includes('login')) state = 'login_required';
      else if (hasBlocked) state = 'blocked';
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

      const articles = [...document.querySelectorAll('article')];
      const assistant = articles[articles.length - 1];
      if (!assistant) return {ok:false, reason:'no assistant article'};
      
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
      const actionButtons = [...assistant.querySelectorAll('button,[role="button"]')];
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


def execute_state_machine(req: Request) -> Result:
    result = Result(ok=False, mode=req.mode, prompt=req.prompt, wrapped_prompt=wrap_prompt(req))
    client = BrowserClient(transport=req.browser_transport)
    result.browserProfile = req.profile
    debug: dict[str, Any] = {'baseUrl': client.base_url, 'browserTransport': client.transport, 'browserProfile': req.profile, 'tabLabel': req.tab_label}
    result.debug = debug

    try:
        target_url = req.conversation_url or CHATGPT_URL
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

        injected = _ensure_prompt_injected(client, req, target_id)
        debug['injected'] = injected
        if not injected or not injected.get('ok'):
            result.error = f'Prompt injection failed: {injected}'
            result.errorCode = 'ERR_NO_TEXTBOX'
            result.nextStep = 'Re-check textbox selectors on ChatGPT homepage.'
            return result

        send = _find_send_button(client, req, target_id)
        debug['sendButton'] = send
        if not send or not send.get('ok'):
            result.error = 'Send button did not appear after prompt injection.'
            result.errorCode = 'ERR_SEND_BUTTON_MISSING'
            result.nextStep = 'Retry prompt injection or refresh ChatGPT page state.'
            return result

        clicked = _click_send(client, req, target_id)
        debug['clicked'] = clicked
        if not clicked or not clicked.get('ok'):
            result.error = f'Send click failed: {clicked}'
            result.errorCode = 'ERR_SUBMISSION_FAILED'
            result.nextStep = 'Verify send button enabled state and DOM label.'
            return result

        # wait for conversation and initial answer render
        _wait(client, req.profile, target_id, 10000)
        snap = client.snapshot(target_id=target_id, profile=req.profile, max_chars=20000, include_urls=True)
        debug['snapshotUrl'] = snap.get('url') if isinstance(snap, dict) else None
        if isinstance(snap, dict) and snap.get('urls'):
            debug['snapshotUrls'] = snap.get('urls')
        url = (snap or {}).get('url') if isinstance(snap, dict) else None
        if not url or '/c/' not in url:
            result.error = 'Submission did not reach a ChatGPT conversation URL.'
            result.errorCode = 'ERR_NO_CONVERSATION_URL'
            result.nextStep = 'Check whether send succeeded but page remained on homepage.'
            return result

        deadline = time.time() + max(15, req.timeout_seconds)
        extracted: dict[str, Any] | None = None
        answer = ''
        stable_count = 0
        last_answer = None
        last_nonempty_extracted: dict[str, Any] | None = None
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
            result.nextStep = 'Snapshot conversation and inspect latest assistant article selectors.'
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
        result.partial = (
            bool(extracted.get('isStreaming'))
            or not good_answer
            or not (bool(extracted.get('hasCopyButton')) or bool(extracted.get('hasPositiveFeedback')) or bool(extracted.get('hasNegativeFeedback')))
        )
        if result.partial:
            result.nextStep = 'Answer may still be streaming or extraction may be incomplete.'
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
            result.nextStep = 'Retry the run; the browser tab was closed or invalidated during execution.'
        elif 'Browser HTTP 401' in msg or 'Unauthorized' in msg:
            result.errorCode = 'ERR_BROWSER_UNAUTHORIZED'
            result.nextStep = 'Use the default OpenClaw CLI browser transport, or provide the current Browser HTTP shared-secret before using --browser-transport http.'
        elif not result.errorCode:
            result.errorCode = 'ERR_UNKNOWN_BLOCKED_STATE'
            result.nextStep = 'Inspect local browser control service, auth token, and ChatGPT DOM state.'
        return result


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
    p.add_argument('--browser-transport', default='cli', choices=['cli', 'http'])
    p.add_argument('--timeout-seconds', type=int, default=45)
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
