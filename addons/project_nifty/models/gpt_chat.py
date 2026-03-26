import json
import os
from urllib import error as urllib_error
from urllib import request as urllib_request

from odoo import api, models, _
from odoo.exceptions import ValidationError

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
except Exception:
    torch = None
    AutoModelForCausalLM = None
    AutoTokenizer = None


class ProjectNiftyAIChat(models.AbstractModel):
    _name = 'project.nifty.ai.chat'
    _description = 'Project Nifty AI Chat Service'

    _tokenizer = None
    _model = None
    _model_name = None

    @api.model
    def _default_model_name(self):
        return self.env['ir.config_parameter'].sudo().get_param('project_nifty.gpt2_model_name', 'gpt2')

    @api.model
    def _ai_provider(self):
        return (self.env['ir.config_parameter'].sudo().get_param('project_nifty.ai_provider', 'google') or 'google').strip().lower()

    @api.model
    def _google_api_key(self):
        key = self.env['ir.config_parameter'].sudo().get_param('project_nifty.google_api_key', '').strip()
        return key or os.getenv('GOOGLE_API_KEY', '').strip()

    @api.model
    def _google_model_name(self):
        return self.env['ir.config_parameter'].sudo().get_param('project_nifty.google_model', 'gemini-2.5-flash').strip() or 'gemini-2.5-flash'

    @api.model
    def _openai_api_key(self):
        key = self.env['ir.config_parameter'].sudo().get_param('project_nifty.openai_api_key', '').strip()
        return key or os.getenv('OPENAI_API_KEY', '').strip()

    @api.model
    def _openai_base_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('project_nifty.openai_base_url', '').strip()
        base_url = base_url or os.getenv('OPENAI_BASE_URL', '').strip()
        return base_url or 'https://api.ai.cc/v1'

    @api.model
    def _openai_model_name(self):
        model = self.env['ir.config_parameter'].sudo().get_param('project_nifty.openai_model', '').strip()
        if model:
            return model
        base = self._openai_base_url().lower()
        if 'api.ai.cc' in base:
            return 'gemini-2.5-flash'
        return 'gpt-4o-mini'

    @api.model
    def _build_chat_messages(self, user_message, history):
        messages = [
            {
                'role': 'system',
                'content': (
                    'Ban la tro ly AI cho module Project Nifty. '
                    'Tra loi bang tieng Viet, ngan gon, lich su, tap trung vao huong dan thuc te cho quan ly du an.'
                ),
            }
        ]
        for item in history:
            role = (item.get('role') or '').strip().lower()
            content = (item.get('content') or '').strip()
            if not content:
                continue
            if role == 'assistant':
                messages.append({'role': 'assistant', 'content': content})
            else:
                messages.append({'role': 'user', 'content': content})
        messages.append({'role': 'user', 'content': user_message})
        return messages[-14:]

    @api.model
    def _build_google_prompt(self, user_message, history):
        lines = [
            'Ban la tro ly AI cho module Project Nifty.',
            'Tra loi bang tieng Viet, ngan gon, lich su, tap trung vao huong dan thuc te cho quan ly du an.',
            '',
        ]
        for item in (history or [])[-8:]:
            role = (item.get('role') or '').strip().lower()
            content = (item.get('content') or '').strip()
            if not content:
                continue
            lines.append(('Tro ly' if role == 'assistant' else 'Nguoi dung') + ': ' + content)
        lines.append('Nguoi dung: ' + user_message)
        lines.append('Tro ly:')
        return '\n'.join(lines)

    @api.model
    def _pending_state_key(self):
        return 'project_nifty.ai_pending_state.%s' % self.env.user.id

    @api.model
    def _load_pending_state(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(self._pending_state_key(), '')
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    @api.model
    def _save_pending_state(self, state):
        if not isinstance(state, dict):
            return
        self.env['ir.config_parameter'].sudo().set_param(self._pending_state_key(), json.dumps(state, ensure_ascii=True))

    @api.model
    def _clear_pending_state(self):
        self.env['ir.config_parameter'].sudo().set_param(self._pending_state_key(), '')

    @api.model
    def _google_generate(self, prompt_text, max_tokens):
        api_key = self._google_api_key()
        if not api_key:
            raise ValidationError(_(
                'Google AI Studio API key is missing. Set system parameter project_nifty.google_api_key or env GOOGLE_API_KEY.'
            ))

        model_name = self._google_model_name()
        endpoint = f'https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}'
        payload = {
            'contents': [
                {
                    'role': 'user',
                    'parts': [
                        {'text': prompt_text}
                    ],
                }
            ],
            'generationConfig': {
                'temperature': 0.5,
                'maxOutputTokens': max_tokens,
            },
        }

        request_data = json.dumps(payload).encode('utf-8')
        req = urllib_request.Request(
            endpoint,
            data=request_data,
            headers={
                'Content-Type': 'application/json',
            },
            method='POST',
        )

        try:
            with urllib_request.urlopen(req, timeout=35) as response:
                body = response.read().decode('utf-8')
        except urllib_error.HTTPError as exc:
            details = exc.read().decode('utf-8', errors='ignore') if hasattr(exc, 'read') else ''
            message = details or str(exc)
            try:
                parsed_err = json.loads(details)
                message = (
                    ((parsed_err.get('error') or {}).get('message'))
                    or ((parsed_err.get('error') or {}).get('status'))
                    or message
                )
            except Exception:
                pass
            raise ValidationError(_('Google AI request failed (%s): %s') % (exc.code, message))
        except urllib_error.URLError as exc:
            raise ValidationError(_('Google AI connection failed: %s') % str(exc))

        try:
            parsed = json.loads(body)
            candidates = parsed.get('candidates') or []
            candidate = candidates[0] if candidates else {}
            parts = ((candidate.get('content') or {}).get('parts') or [])
            content_text = ''.join((part.get('text') or '') for part in parts).strip()
            finish_reason = (candidate.get('finishReason') or '').strip().upper()
            return content_text, finish_reason, model_name
        except Exception as exc:
            raise ValidationError(_('Invalid Google AI response: %s') % str(exc))

    @api.model
    def _continue_google_chat(self, pending_state):
        partial_reply = (pending_state.get('partial_reply') or '').strip()
        if not partial_reply:
            self._clear_pending_state()
            return {
                'reply': '',
                'model': self._google_model_name(),
                'provider': 'google',
                'pending_continuation': False,
            }

        continue_prompt = (
            'Ban dang tra loi dang do. Hay tiep tuc ngay sau noi dung ben duoi, '
            'khong lap lai, khong mo dau lai, giu cung van phong:\n\n' + partial_reply
        )
        content, finish_reason, model_name = self._google_generate(continue_prompt, 360)
        pending = bool(content) and finish_reason == 'MAX_TOKENS'

        if pending:
            pending_state['partial_reply'] = (partial_reply + '\n' + content).strip()
            self._save_pending_state(pending_state)
        else:
            self._clear_pending_state()

        return {
            'reply': content,
            'model': model_name,
            'provider': 'google',
            'pending_continuation': pending,
        }

    @api.model
    def _call_google_chat(self, user_message, history):
        prompt = self._build_google_prompt(user_message, history)
        content, finish_reason, model_name = self._google_generate(prompt, 700)

        pending = bool(content) and finish_reason == 'MAX_TOKENS'
        if pending:
            self._save_pending_state({
                'provider': 'google',
                'model': model_name,
                'history': (history or [])[-10:],
                'user_message': user_message,
                'partial_reply': content,
            })
        else:
            self._clear_pending_state()

        if not content:
            content = _('Mình chưa tạo được phản hồi. Bạn thử hỏi lại chi tiết hơn nhé.')

        return {
            'reply': content,
            'model': model_name,
            'provider': 'google',
            'pending_continuation': pending,
        }

    @api.model
    def _continue_openai_chat(self, pending_state):
        api_key = self._openai_api_key()
        if not api_key:
            self._clear_pending_state()
            return {
                'reply': '',
                'model': self._openai_model_name(),
                'provider': 'openai',
                'pending_continuation': False,
            }

        partial_reply = (pending_state.get('partial_reply') or '').strip()
        if not partial_reply:
            self._clear_pending_state()
            return {
                'reply': '',
                'model': self._openai_model_name(),
                'provider': 'openai',
                'pending_continuation': False,
            }

        base_url = self._openai_base_url().rstrip('/')
        endpoint = base_url if base_url.endswith('/chat/completions') else f'{base_url}/chat/completions'
        payload = {
            'model': self._openai_model_name(),
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        'Ban la tro ly AI cho module Project Nifty. '
                        'Tra loi bang tieng Viet, ngan gon, lich su, tap trung vao huong dan thuc te cho quan ly du an.'
                    ),
                },
                {
                    'role': 'user',
                    'content': (
                        'Hay tiep tuc ngay sau noi dung dang do sau day, khong lap lai doan da co:\n\n' + partial_reply
                    ),
                },
            ],
            'temperature': 0.4,
            'max_tokens': 220,
        }

        request_data = json.dumps(payload).encode('utf-8')
        req = urllib_request.Request(
            endpoint,
            data=request_data,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
            },
            method='POST',
        )

        try:
            with urllib_request.urlopen(req, timeout=35) as response:
                body = response.read().decode('utf-8')
        except Exception:
            self._clear_pending_state()
            return {
                'reply': '',
                'model': self._openai_model_name(),
                'provider': 'openai',
                'pending_continuation': False,
            }

        parsed = json.loads(body)
        choice = ((parsed.get('choices') or [{}])[0])
        content = (((choice.get('message') or {}).get('content')) or '').strip()
        finish_reason = (choice.get('finish_reason') or '').strip().lower()
        pending = bool(content) and finish_reason in ('length', 'max_tokens')

        if pending:
            pending_state['partial_reply'] = (partial_reply + '\n' + content).strip()
            self._save_pending_state(pending_state)
        else:
            self._clear_pending_state()

        return {
            'reply': content,
            'model': self._openai_model_name(),
            'provider': 'openai',
            'pending_continuation': pending,
        }

    @api.model
    def _call_openai_chat(self, user_message, history):
        api_key = self._openai_api_key()
        if not api_key:
            raise ValidationError(_(
                'OpenAI API key is missing. Set system parameter project_nifty.openai_api_key or env OPENAI_API_KEY.'
            ))

        base_url = self._openai_base_url().rstrip('/')
        if base_url.endswith('/chat/completions'):
            endpoint = base_url
        else:
            endpoint = f'{base_url}/chat/completions'
        payload = {
            'model': self._openai_model_name(),
            'messages': self._build_chat_messages(user_message, history),
            'temperature': 0.5,
            'max_tokens': 300,
        }

        request_data = json.dumps(payload).encode('utf-8')
        req = urllib_request.Request(
            endpoint,
            data=request_data,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
            },
            method='POST',
        )

        try:
            with urllib_request.urlopen(req, timeout=35) as response:
                body = response.read().decode('utf-8')
        except urllib_error.HTTPError as exc:
            details = exc.read().decode('utf-8', errors='ignore') if hasattr(exc, 'read') else ''
            raise ValidationError(_('OpenAI request failed (%s): %s') % (exc.code, details or str(exc)))
        except urllib_error.URLError as exc:
            raise ValidationError(_('OpenAI connection failed: %s') % str(exc))

        try:
            parsed = json.loads(body)
            choice = parsed['choices'][0]
            content = ((choice.get('message') or {}).get('content') or '').strip()
            finish_reason = (choice.get('finish_reason') or '').strip().lower()
        except Exception as exc:
            raise ValidationError(_('Invalid OpenAI response: %s') % str(exc))

        pending = bool(content) and finish_reason in ('length', 'max_tokens')
        if pending:
            self._save_pending_state({
                'provider': 'openai',
                'model': self._openai_model_name(),
                'history': (history or [])[-10:],
                'user_message': user_message,
                'partial_reply': content,
            })
        else:
            self._clear_pending_state()

        if not content:
            content = _('Mình chưa tạo được phản hồi. Bạn thử hỏi lại chi tiết hơn nhé.')

        return {
            'reply': content,
            'model': self._openai_model_name(),
            'provider': 'openai',
            'pending_continuation': pending,
        }

    @api.model
    def _call_gpt2_chat(self, user_message, history):
        tokenizer, model = self._ensure_model_loaded()
        history = history or []
        history = [line for line in history if isinstance(line, dict)]
        history = history[-6:]

        lines = [
            'You are Project Nifty Assistant.',
            'Reply in concise Vietnamese, practical and polite.',
            'If user asks about project management, tasks, documents, files, team workflow, give actionable guidance.',
            '',
        ]

        for item in history:
            role = (item.get('role') or '').strip().lower()
            content = (item.get('content') or '').strip()
            if not content:
                continue
            if role == 'assistant':
                lines.append(f'Assistant: {content}')
            else:
                lines.append(f'User: {content}')

        lines.append(f'User: {user_message}')
        lines.append('Assistant:')
        prompt = '\n'.join(lines)

        # Keep context under GPT-2 limit to avoid generation errors on long chats.
        inputs = tokenizer(prompt, return_tensors='pt', truncation=True, max_length=900)
        input_ids = inputs['input_ids']
        attention_mask = inputs.get('attention_mask')

        with torch.no_grad(): # pyright: ignore[reportOptionalMemberAccess]
            output_ids = model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_new_tokens=120,
                do_sample=True,
                temperature=0.8,
                top_p=0.92,
                repetition_penalty=1.12,
                no_repeat_ngram_size=3,
                eos_token_id=tokenizer.eos_token_id,
                pad_token_id=tokenizer.pad_token_id,
            )

        generated_ids = output_ids[0][input_ids.shape[-1]:]
        generated = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

        if 'User:' in generated:
            generated = generated.split('User:')[0].strip()
        if generated.startswith('Assistant:'):
            generated = generated[len('Assistant:'):].strip()
        if not generated:
            generated = _('Mình đang xử lý câu hỏi. Bạn thử diễn đạt cụ thể hơn một chút nhé.')

        return {
            'reply': generated,
            'model': self.__class__._model_name or self._default_model_name(),
            'provider': 'gpt2',
            'pending_continuation': False,
        }

    @api.model
    def _continue_pending_reply(self):
        pending_state = self._load_pending_state()
        if not pending_state:
            return {
                'reply': '',
                'provider': self._ai_provider(),
                'pending_continuation': False,
            }

        provider = (pending_state.get('provider') or '').strip().lower()
        if provider in ('google', 'gemini', 'google_ai_studio'):
            return self._continue_google_chat(pending_state)
        if provider == 'openai':
            return self._continue_openai_chat(pending_state)

        self._clear_pending_state()
        return {
            'reply': '',
            'provider': provider or self._ai_provider(),
            'pending_continuation': False,
        }

    @api.model
    def _ensure_model_loaded(self):
        if not AutoTokenizer or not AutoModelForCausalLM or not torch:
            raise ValidationError(_(
                'GPT-2 dependencies are missing. Install transformers and torch in the Python environment.'
            ))

        model_name = self._default_model_name()
        if self.__class__._model is not None and self.__class__._tokenizer is not None and self.__class__._model_name == model_name:
            return self.__class__._tokenizer, self.__class__._model

        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForCausalLM.from_pretrained(model_name)
        model.eval()

        if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
            tokenizer.pad_token = tokenizer.eos_token

        self.__class__._tokenizer = tokenizer
        self.__class__._model = model
        self.__class__._model_name = model_name
        return tokenizer, model

    @api.model
    def generate_advice_once(self, prompt):
        provider = self._ai_provider()
        message = (prompt or '').strip()
        if not message:
            return {
                'reply': _('Khong co noi dung de phan tich.'),
                'provider': provider,
                'error': True,
            }

        try:
            if provider in ('google', 'gemini', 'google_ai_studio'):
                content, _finish_reason, model_name = self._google_generate(self._build_google_prompt(message, []), 520)
                return {
                    'reply': content or _('AI khong tra ve noi dung.'),
                    'provider': 'google',
                    'model': model_name,
                    'pending_continuation': False,
                }

            if provider == 'gpt2':
                result = self._call_gpt2_chat(message, [])
                result['pending_continuation'] = False
                return result

            result = self._call_openai_chat(message, [])
            # Business suggestions should not interfere with chat continuation state.
            self._clear_pending_state()
            result['pending_continuation'] = False
            return result
        except ValidationError as exc:
            return {
                'reply': _('Loi cau hinh AI: %s') % str(exc),
                'provider': provider,
                'error': True,
            }
        except Exception as exc:
            return {
                'reply': _('Khong the tao goi y AI: %s') % str(exc),
                'provider': provider,
                'error': True,
            }

    @api.model
    def quick_support_reply(self, message, history=None, continue_last=False):
        provider = self._ai_provider()
        try:
            if continue_last:
                return self._continue_pending_reply()

            user_message = (message or '').strip()
            if not user_message:
                return {
                    'reply': _('Vui long nhap noi dung truoc khi gui.'),
                    'provider': provider,
                    'error': True,
                    'pending_continuation': False,
                }
            history = history or []
            history = [line for line in history if isinstance(line, dict)]

            if provider in ('google', 'gemini', 'google_ai_studio'):
                return self._call_google_chat(user_message, history)
            if provider == 'gpt2':
                return self._call_gpt2_chat(user_message, history)
            return self._call_openai_chat(user_message, history)
        except ValidationError as exc:
            return {
                'reply': _('Loi cau hinh AI: %s') % str(exc),
                'provider': provider,
                'error': True,
                'pending_continuation': False,
            }
        except Exception as exc:
            return {
                'reply': _('Chat service failed: %s') % str(exc),
                'provider': provider,
                'error': True,
                'pending_continuation': False,
            }

    @api.model
    def quick_support_clear(self):
        self._clear_pending_state()
        return {'ok': True}

    @api.model
    def get_allowed_menu_ids(self):
        root_menu = self.env.ref('project_nifty.menu_project_nifty_root', raise_if_not_found=False)
        if not root_menu:
            return []
        menus = self.env['ir.ui.menu'].sudo().search([('id', 'child_of', root_menu.id)])
        menu_ids = set(menus.ids)
        menu_ids.add(root_menu.id)
        return list(menu_ids)