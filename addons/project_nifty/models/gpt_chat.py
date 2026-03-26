import json
import os
import re
from urllib import error as urllib_error
from urllib import request as urllib_request

from odoo import api, fields, models, _
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
    def _int_param(self, key, default_value, min_value=1, max_value=20000):
        raw = self.env['ir.config_parameter'].sudo().get_param(key, str(default_value)).strip()
        try:
            value = int(raw)
        except Exception:
            value = default_value
        return max(min_value, min(max_value, value))

    @api.model
    def _build_system_prompt(self, runtime_context=''):
        base = (
            'Bạn là trợ lý AI cho module Project Nifty. '
            'Trả lời bằng tiếng Việt, tự nhiên, lịch sự, tập trung vào hướng dẫn thực tế cho quản lý dự án. '
            'Nếu dữ liệu chưa đủ, hãy nói rõ cần bổ sung gì. '
            'Khi người dùng hỏi thống kê, ưu tiên dùng số liệu từ bối cảnh được cung cấp bên dưới.'
        )
        if not runtime_context:
            return base
        return base + '\n\nDữ liệu hệ thống hiện tại:\n' + runtime_context

    @api.model
    def _compact_history(self, history):
        keep_turns = self._int_param('project_nifty.ai_chat_history_turns', 10, min_value=2, max_value=30)
        max_item_chars = self._int_param('project_nifty.ai_chat_history_item_chars', 600, min_value=120, max_value=2000)
        compact = []
        for item in (history or [])[-keep_turns:]:
            if not isinstance(item, dict):
                continue
            role = (item.get('role') or '').strip().lower()
            content = (item.get('content') or '').strip()
            if not content:
                continue
            if len(content) > max_item_chars:
                content = content[:max_item_chars].rstrip() + ' ...'
            compact.append({'role': role if role == 'assistant' else 'user', 'content': content})
        return compact

    @api.model
    def _build_runtime_project_context(self, context_payload=None):
        Project = self.env['project.nifty']
        Task = self.env['project.nifty.task']
        Document = self.env['project.nifty.document']
        File = self.env['project.nifty.file']
        Team = self.env['project.nifty.team']

        payload = context_payload or {}
        model = (payload.get('model') or '').strip().lower()
        res_id = payload.get('res_id')
        try:
            res_id = int(res_id) if res_id else 0
        except Exception:
            res_id = 0

        project = False
        if model == 'project.nifty' and res_id:
            project = Project.browse(res_id).exists()
        elif model == 'project.nifty.task' and res_id:
            task = Task.browse(res_id).exists()
            project = task.project_id if task else False
        elif model == 'project.nifty.document' and res_id:
            doc = Document.browse(res_id).exists()
            project = doc.project_id if doc else False
        elif model == 'project.nifty.file' and res_id:
            rec_file = File.browse(res_id).exists()
            project = rec_file.project_id if rec_file else False

        project_domain = [('project_id', '=', project.id)] if project else []
        project_count = Project.search_count([])
        task_count = Task.search_count(project_domain)
        doc_count = Document.search_count(project_domain)
        file_count = File.search_count(project_domain)
        team_count = Team.search_count([])

        status_rows = Task.read_group(project_domain, ['id:count'], ['status'], lazy=False)
        status_map = {
            'todo': 0,
            'in_progress': 0,
            'ready_review': 0,
            'approved': 0,
            'rejected': 0,
        }
        for row in status_rows:
            status_key = row.get('status')
            if status_key in status_map:
                count_value = row.get('id_count', row.get('__count', row.get('status_count', 0)))
                status_map[status_key] = int(count_value or 0)

        if project:
            project_line = '- Project hiện tại: %s (ID: %s, Team: %s, Thành viên: %s, Trạng thái: %s)' % (
                project.name,
                project.id,
                (project.team_id.name or 'N/A'),
                len(project.member_ids),
                project.status,
            )
            project_domain_text = '- Phạm vi dữ liệu: CHỈ DỰ ÁN HIỆN TẠI'
            sample_projects = [project]
        else:
            project_line = '- Project hiện tại: Không xác định từ màn hình đang mở'
            project_domain_text = '- Phạm vi dữ liệu: TOÀN BỘ PROJECT NIFTY mà user đang có quyền xem'
            sample_projects = Project.search([], limit=5, order='id desc')

        sample_tasks = Task.search(project_domain, limit=8, order='priority desc, id desc')
        sample_docs = Document.search(project_domain, limit=6, order='id desc')
        sample_files = File.search(project_domain, limit=6, order='upload_date desc, id desc')

        lines = [
            project_domain_text,
            project_line,
            '- Tổng quan: %s projects, %s teams, %s tasks, %s documents, %s files.' % (
                project_count, team_count, task_count, doc_count, file_count,
            ),
            '- Task theo trạng thái: todo=%s, in_progress=%s, ready_review=%s, approved=%s, rejected=%s.' % (
                status_map['todo'],
                status_map['in_progress'],
                status_map['ready_review'],
                status_map['approved'],
                status_map['rejected'],
            ),
            '- Danh sách project tiêu biểu: %s' % (', '.join(sample_projects.mapped('name')) or 'Không có'),
            '- Task gần đây: %s' % (', '.join(sample_tasks.mapped('name')) or 'Không có'),
            '- Document gần đây: %s' % (', '.join(sample_docs.mapped('name')) or 'Không có'),
            '- File gần đây: %s' % (', '.join(sample_files.mapped('name')) or 'Không có'),
        ]
        return '\n'.join(lines)

    @api.model
    def _resolve_active_project(self, context_payload=None):
        payload = context_payload or {}
        model = (payload.get('model') or '').strip().lower()
        res_id = payload.get('res_id')
        try:
            res_id = int(res_id) if res_id else 0
        except Exception:
            res_id = 0

        project = False
        if model == 'project.nifty' and res_id:
            project = self.env['project.nifty'].browse(res_id).exists()
        elif model == 'project.nifty.task' and res_id:
            task = self.env['project.nifty.task'].browse(res_id).exists()
            project = task.project_id if task else False
        elif model == 'project.nifty.document' and res_id:
            document = self.env['project.nifty.document'].browse(res_id).exists()
            project = document.project_id if document else False
        elif model == 'project.nifty.file' and res_id:
            rec_file = self.env['project.nifty.file'].browse(res_id).exists()
            project = rec_file.project_id if rec_file else False
        return project

    @api.model
    def _normalize_text(self, value):
        text = (value or '').strip().lower()
        replacements = {
            'á': 'a', 'à': 'a', 'ả': 'a', 'ã': 'a', 'ạ': 'a',
            'ă': 'a', 'ắ': 'a', 'ằ': 'a', 'ẳ': 'a', 'ẵ': 'a', 'ặ': 'a',
            'â': 'a', 'ấ': 'a', 'ầ': 'a', 'ẩ': 'a', 'ẫ': 'a', 'ậ': 'a',
            'é': 'e', 'è': 'e', 'ẻ': 'e', 'ẽ': 'e', 'ẹ': 'e',
            'ê': 'e', 'ế': 'e', 'ề': 'e', 'ể': 'e', 'ễ': 'e', 'ệ': 'e',
            'í': 'i', 'ì': 'i', 'ỉ': 'i', 'ĩ': 'i', 'ị': 'i',
            'ó': 'o', 'ò': 'o', 'ỏ': 'o', 'õ': 'o', 'ọ': 'o',
            'ô': 'o', 'ố': 'o', 'ồ': 'o', 'ổ': 'o', 'ỗ': 'o', 'ộ': 'o',
            'ơ': 'o', 'ớ': 'o', 'ờ': 'o', 'ở': 'o', 'ỡ': 'o', 'ợ': 'o',
            'ú': 'u', 'ù': 'u', 'ủ': 'u', 'ũ': 'u', 'ụ': 'u',
            'ư': 'u', 'ứ': 'u', 'ừ': 'u', 'ử': 'u', 'ữ': 'u', 'ự': 'u',
            'ý': 'y', 'ỳ': 'y', 'ỷ': 'y', 'ỹ': 'y', 'ỵ': 'y',
            'đ': 'd',
        }
        for src, dst in replacements.items():
            text = text.replace(src, dst)
        return text

    @api.model
    def _deterministic_support_reply(self, user_message, context_payload=None):
        text = self._normalize_text(user_message)
        if not text:
            return None

        has_count_intent = any(token in text for token in ['bao nhieu', 'so luong', 'thong ke', 'tong so'])
        has_list_intent = any(token in text for token in ['liet ke', 'danh sach', 'co nhung', 'gom nhung', 'show'])
        has_risk_intent = any(token in text for token in [
            'rui ro', 'canh bao', 'qua han', 'tre han', 'cham tien do', 'tien do', 'nghen', 'ton dong', 'block', 'unblock'
        ])
        asks_project = any(token in text for token in ['project', 'du an'])
        asks_task = any(token in text for token in ['task', 'cong viec'])
        asks_doc = any(token in text for token in ['document', 'tai lieu'])
        asks_file = any(token in text for token in ['file', 'tep'])
        asks_status = any(token in text for token in ['trang thai', 'status'])

        if not (has_count_intent or has_list_intent or asks_status or has_risk_intent):
            return None

        project = self._resolve_active_project(context_payload)
        project_domain = [('project_id', '=', project.id)] if project else []

        def _short_list(values, limit=5):
            items = [v for v in (values or []) if v]
            if len(items) <= limit:
                return ', '.join(items) or 'Không có'
            hidden = len(items) - limit
            return '%s ... (+%s mục nữa)' % (', '.join(items[:limit]), hidden)

        Project = self.env['project.nifty']
        Task = self.env['project.nifty.task']
        Document = self.env['project.nifty.document']
        File = self.env['project.nifty.file']

        status_rows = Task.read_group(project_domain, ['id:count'], ['status'], lazy=False)
        status_map = {
            'todo': 0,
            'in_progress': 0,
            'ready_review': 0,
            'approved': 0,
            'rejected': 0,
        }
        for row in status_rows:
            key = row.get('status')
            if key in status_map:
                count_value = row.get('id_count', row.get('__count', row.get('status_count', 0)))
                status_map[key] = int(count_value or 0)

        scope_label = 'trong project "%s"' % project.name if project else 'trong toàn bộ module Project Nifty'
        overview_lines = []
        detail_lines = []
        action_lines = []

        if has_count_intent or asks_status:
            project_count = Project.search_count([])
            task_count = Task.search_count(project_domain)
            doc_count = Document.search_count(project_domain)
            file_count = File.search_count(project_domain)

            if asks_project or (not asks_task and not asks_doc and not asks_file):
                overview_lines.append('- Số lượng Project: %s' % project_count)
            if asks_task or asks_status or (not asks_project and not asks_doc and not asks_file):
                overview_lines.append('- Số lượng Task %s: %s' % (scope_label, task_count))
                detail_lines.append('- Trạng thái Task: Todo=%s, In Progress=%s, Ready Review=%s, Approved=%s, Rejected=%s' % (
                    status_map['todo'],
                    status_map['in_progress'],
                    status_map['ready_review'],
                    status_map['approved'],
                    status_map['rejected'],
                ))
            if asks_doc or (not asks_project and not asks_task and not asks_file):
                overview_lines.append('- Số lượng Document %s: %s' % (scope_label, doc_count))
            if asks_file or (not asks_project and not asks_task and not asks_doc):
                overview_lines.append('- Số lượng File %s: %s' % (scope_label, file_count))

        if has_risk_intent:
            today = fields.Date.context_today(self)
            overdue_domain = list(project_domain) + [
                ('deadline', '!=', False),
                ('deadline', '<', today),
                ('status', '!=', 'approved'),
            ]
            overdue_tasks = Task.search(overdue_domain, order='deadline asc, priority desc, id asc', limit=8)
            overdue_count = Task.search_count(overdue_domain)

            blocked_domain = list(project_domain) + [('status', 'in', ['rejected', 'ready_review'])]
            blocked_count = Task.search_count(blocked_domain)

            in_progress_domain = list(project_domain) + [('status', '=', 'in_progress')]
            in_progress_count = Task.search_count(in_progress_domain)

            pending_project_domain = [('status', '=', 'pending_approval')]
            if project:
                pending_project_domain = [('id', '=', project.id), ('status', '=', 'pending_approval')]
            pending_project_count = Project.search_count(pending_project_domain)

            overview_lines.append('- Cảnh báo tiến độ %s:' % scope_label)
            detail_lines.append('- Task quá hạn: %s' % overdue_count)
            detail_lines.append('- Task cần xử lý ngay (ready_review/rejected): %s' % blocked_count)
            detail_lines.append('- Task đang làm: %s' % in_progress_count)
            detail_lines.append('- Project pending approval: %s' % pending_project_count)

            if overdue_tasks:
                overdue_preview = []
                for task in overdue_tasks:
                    overdue_preview.append('%s (deadline %s, status %s)' % (
                        task.name,
                        task.deadline,
                        task.status,
                    ))
                detail_lines.append('- Top task quá hạn: %s' % _short_list(overdue_preview, limit=4))
            else:
                detail_lines.append('- Top task quá hạn: Không có')

            if overdue_count:
                action_lines.append('- Ưu tiên xử lý task quá hạn trước, chia theo mức độ priority.')
            if blocked_count:
                action_lines.append('- Review nhanh các task ready_review/rejected để giải phóng luồng công việc.')
            if pending_project_count:
                action_lines.append('- Project pending approval cần check điều kiện Approved tất cả task trước khi chốt.')
            if not action_lines:
                action_lines.append('- Không thấy cảnh báo lớn. Duy trì nhịp cập nhật trạng thái hằng ngày.')

        if has_list_intent:
            if asks_project:
                project_names = Project.search([], order='id desc', limit=10).mapped('name')
                detail_lines.append('- Project gần đây: %s' % _short_list(project_names, limit=6))
            if asks_task or (not asks_project and not asks_doc and not asks_file):
                task_names = Task.search(project_domain, order='priority desc, id desc', limit=10).mapped('name')
                detail_lines.append('- Task gần đây %s: %s' % (scope_label, _short_list(task_names, limit=6)))
            if asks_doc:
                doc_names = Document.search(project_domain, order='id desc', limit=10).mapped('name')
                detail_lines.append('- Document gần đây %s: %s' % (scope_label, _short_list(doc_names, limit=6)))
            if asks_file:
                file_names = File.search(project_domain, order='upload_date desc, id desc', limit=10).mapped('name')
                detail_lines.append('- File gần đây %s: %s' % (scope_label, _short_list(file_names, limit=6)))

        if not overview_lines and not detail_lines:
            return None

        context_hint = 'Đang bám theo project hiện tại: %s.' % project.name if project else 'Không tìm thấy context một project cụ thể, đang thống kê tổng quan.'
        blocks = [
            '**Tổng quan**',
            '\n'.join(overview_lines) if overview_lines else '- Không có dữ liệu tổng quan.',
            '',
            '**Chi tiết**',
            '\n'.join(detail_lines) if detail_lines else '- Không có dữ liệu chi tiết.',
        ]
        if action_lines:
            blocks.extend([
                '',
                '**Đề xuất hành động**',
                '\n'.join(action_lines),
            ])
        blocks.extend(['', context_hint])
        reply = '\n'.join(blocks)

        return {
            'reply': reply,
            'provider': 'project_nifty_rule_engine',
            'model': 'project_nifty_data',
            'pending_continuation': False,
        }

    @api.model
    def _build_chat_messages(self, user_message, history, runtime_context=''):
        messages = [
            {
                'role': 'system',
                'content': self._build_system_prompt(runtime_context),
            }
        ]
        for item in self._compact_history(history):
            role = (item.get('role') or '').strip().lower()
            content = (item.get('content') or '').strip()
            if not content:
                continue
            if role == 'assistant':
                messages.append({'role': 'assistant', 'content': content})
            else:
                messages.append({'role': 'user', 'content': content})
        messages.append({'role': 'user', 'content': user_message})
        return messages

    @api.model
    def _build_google_prompt(self, user_message, history, runtime_context=''):
        lines = [
            self._build_system_prompt(runtime_context),
            '',
        ]
        for item in self._compact_history(history):
            role = (item.get('role') or '').strip().lower()
            content = (item.get('content') or '').strip()
            if not content:
                continue
            lines.append(('Trợ lý' if role == 'assistant' else 'Người dùng') + ': ' + content)
        lines.append('Người dùng: ' + user_message)
        lines.append('Trợ lý:')
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
    def _pending_action_state_key(self):
        return 'project_nifty.ai_pending_action.%s' % self.env.user.id

    @api.model
    def _load_pending_action_state(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(self._pending_action_state_key(), '')
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    @api.model
    def _save_pending_action_state(self, state):
        if not isinstance(state, dict):
            return
        self.env['ir.config_parameter'].sudo().set_param(
            self._pending_action_state_key(),
            json.dumps(state, ensure_ascii=True),
        )

    @api.model
    def _clear_pending_action_state(self):
        self.env['ir.config_parameter'].sudo().set_param(self._pending_action_state_key(), '')

    @api.model
    def _is_confirm_message(self, message):
        text = self._normalize_text(message or '')
        return text in {'xac nhan', 'dong y', 'ok', 'yes', 'confirm', 'thuc hien'}

    @api.model
    def _is_cancel_message(self, message):
        text = self._normalize_text(message or '')
        return text in {'huy', 'cancel', 'bo qua', 'khong'}

    @api.model
    def _parse_agent_fields(self, user_message):
        key_map = {
            'name': 'name',
            'ten': 'name',
            'project': 'project',
            'duan': 'project',
            'team': 'team',
            'nhom': 'team',
            'tasklist': 'tasklist',
            'danhsachtask': 'tasklist',
            'mota': 'description',
            'description': 'description',
            'noidung': 'content',
            'content': 'content',
            'deadline': 'deadline',
            'han': 'deadline',
            'priority': 'priority',
            'uutien': 'priority',
            'giacho': 'assignees',
            'assignees': 'assignees',
        }
        parsed = {}
        chunks = re.split(r'[\n;]+', user_message or '')
        for raw in chunks:
            part = (raw or '').strip()
            if not part or (':' not in part and '=' not in part):
                continue
            if ':' in part:
                key, value = part.split(':', 1)
            else:
                key, value = part.split('=', 1)
            normalized_key = self._normalize_text(key).replace(' ', '')
            canonical = key_map.get(normalized_key)
            if canonical:
                parsed[canonical] = (value or '').strip()
        return parsed

    @api.model
    def _extract_first(self, text, patterns):
        for pattern in patterns:
            match = re.search(pattern, text or '', flags=re.IGNORECASE)
            if match:
                value = (match.group(1) or '').strip(' .,;:')
                if value:
                    return value
        return ''

    @api.model
    def _normalize_deadline_value(self, deadline_value):
        value = (deadline_value or '').strip()
        if not value:
            return ''
        iso = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', value)
        if iso:
            return value
        dmy = re.match(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$', value)
        if dmy:
            day, month, year = dmy.groups()
            return '%s-%02d-%02d' % (year, int(month), int(day))
        return value

    @api.model
    def _parse_agent_fields_natural(self, user_message, intent):
        text = user_message or ''
        parsed = {}

        if intent == 'create_project':
            parsed['name'] = self._extract_first(text, [
                r'(?:tao|tạo|create)\s+(?:project|du an|dự án)\s+["“]?([^"”\n,;]+)',
                r'project\s+["“]?([^"”\n,;]+)',
                r'du an\s+["“]?([^"”\n,;]+)',
                r'dự án\s+["“]?([^"”\n,;]+)',
            ])
            parsed['team'] = self._extract_first(text, [
                r'(?:team|nhom|nhóm)\s*[:=]?\s*["“]?([^"”\n,;]+)',
            ])
            parsed['description'] = self._extract_first(text, [
                r'(?:mo ta|mô tả|description|noi dung|nội dung)\s*[:=]?\s*(.+)$',
            ])

        elif intent == 'create_task':
            parsed['name'] = self._extract_first(text, [
                r'(?:tao|tạo|create)\s+(?:task|cong viec|công việc)\s+["“]?(.+?)["”]?(?=\s+(?:cho\s+(?:project|du an|dự án)|project|du an|dự án|han|hạn|deadline|uu tien|ưu tiên|priority|giao cho|assign|assignees?|noi dung|nội dung|mo ta|mô tả)\b|\s+\d{4}-\d{2}-\d{2}|\s+\d{1,2}[/-]\d{1,2}[/-]\d{4}|\s+\S+:|$)',
                r'task\s+["“]?(.+?)["”]?(?=\s+(?:cho\s+(?:project|du an|dự án)|project|du an|dự án|han|hạn|deadline|uu tien|ưu tiên|priority|giao cho|assign|assignees?|noi dung|nội dung|mo ta|mô tả)\b|\s+\d{4}-\d{2}-\d{2}|\s+\d{1,2}[/-]\d{1,2}[/-]\d{4}|\s+\S+:|$)',
            ])
            parsed['project'] = self._extract_first(text, [
                r'(?:project|du an|dự án)\s*[:=]?\s*["“]?(.+?)["”]?(?=\s+(?:han|hạn|deadline|uu tien|ưu tiên|priority|giao cho|assign|assignees?|noi dung|nội dung|mo ta|mô tả)\b|\s+\d{4}-\d{2}-\d{2}|\s+\d{1,2}[/-]\d{1,2}[/-]\d{4}|\s+\S+:|$)',
            ])
            parsed['tasklist'] = self._extract_first(text, [
                r'(?:tasklist|task list|list)\s*[:=]?\s*["“]?([^"”\n,;]+)',
            ])
            parsed['deadline'] = self._extract_first(text, [
                r'(?:deadline|han|hạn)\s*[:=]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            ])
            parsed['deadline'] = self._normalize_deadline_value(parsed.get('deadline'))
            parsed['priority'] = self._extract_first(text, [
                r'(?:priority|uu tien|ưu tiên)\s*[:=]?\s*([^\n,;]+)',
            ])
            parsed['assignees'] = self._extract_first(text, [
                r'(?:giao\s+cho|assign(?:ed)?\s+to|assignees?)\s*[:=]?\s*(.+)$',
            ])
            parsed['description'] = self._extract_first(text, [
                r'(?:mo ta|mô tả|description|noi dung|nội dung)\s*[:=]?\s*(.+)$',
            ])

        elif intent == 'create_document':
            parsed['name'] = self._extract_first(text, [
                r'(?:tao|tạo|create)\s+(?:document|tai lieu|tài liệu)\s+["“]?(.+?)["”]?(?=\s+(?:cho\s+(?:project|du an|dự án)|project|du an|dự án|noi dung|nội dung|content|mo ta|mô tả|description)\b|\s+\S+:|$)',
                r'(?:document|tai lieu|tài liệu)\s+["“]?(.+?)["”]?(?=\s+(?:cho\s+(?:project|du an|dự án)|project|du an|dự án|noi dung|nội dung|content|mo ta|mô tả|description)\b|\s+\S+:|$)',
            ])
            parsed['project'] = self._extract_first(text, [
                r'(?:project|du an|dự án)\s*[:=]?\s*["“]?(.+?)["”]?(?=\s+(?:noi dung|nội dung|content|mo ta|mô tả|description)\b|\s+\S+:|$)',
            ])
            parsed['content'] = self._extract_first(text, [
                r'(?:noi dung|nội dung|content|mo ta|mô tả|description)\s*[:=]?\s*(.+)$',
            ])

        return {k: v for k, v in parsed.items() if v}

    @api.model
    def _merge_agent_fields(self, base_fields, natural_fields):
        merged = dict(base_fields or {})
        for key, value in (natural_fields or {}).items():
            if not merged.get(key):
                merged[key] = value
        return merged

    @api.model
    def _merge_agent_fields_override(self, base_fields, updates):
        merged = dict(base_fields or {})
        for key, value in (updates or {}).items():
            if value:
                merged[key] = value
        return merged

    @api.model
    def _missing_required_agent_fields(self, intent, fields_map, context_payload=None):
        missing = []
        fields_map = fields_map or {}
        context_payload = context_payload or {}

        if intent == 'create_project':
            if not (fields_map.get('name') or '').strip():
                missing.append('name')
            if not (fields_map.get('team') or '').strip():
                missing.append('team')
        elif intent == 'create_task':
            if not (fields_map.get('name') or '').strip():
                missing.append('name')
            has_project = (fields_map.get('project') or '').strip() or self._resolve_active_project(context_payload)
            if not has_project:
                missing.append('project')

        return missing

    @api.model
    def _build_missing_fields_prompt(self, intent, missing_fields, fields_map=None):
        missing_fields = missing_fields or []
        next_field = missing_fields[0] if missing_fields else ''
        return self._build_single_missing_field_prompt(intent, next_field, fields_map or {})

    @api.model
    def _build_single_missing_field_prompt(self, intent, missing_field, fields_map=None):
        fields_map = fields_map or {}
        known_name = (fields_map.get('name') or '').strip()
        known_project = (fields_map.get('project') or '').strip()
        known_team = (fields_map.get('team') or '').strip()

        if intent == 'create_project' and missing_field == 'name':
            return 'Mình chưa có tên project. Bạn cho mình tên project nhé? Ví dụ: Du an CRM Q2'

        if intent == 'create_project' and missing_field == 'team':
            if known_name:
                return 'Oke, mình đã nhận tên project "%s". Bạn cho mình tên team phụ trách nhé?' % known_name
            return 'Bạn cho mình tên team phụ trách project nhé? Ví dụ: Nhom Cong nghe A'

        if intent == 'create_task' and missing_field == 'name':
            if known_project:
                return 'Mình đã nhận project "%s". Bạn cho mình tên task cần tạo nhé?' % known_project
            return 'Mình chưa có tên task. Bạn cho mình tên task nhé?'

        if intent == 'create_task' and missing_field == 'project':
            if known_name:
                return 'Mình đã nhận tên task "%s". Bạn cho mình tên project nhé?' % known_name
            return 'Bạn cho mình tên project để gắn task nhé? Ví dụ: MAIL TEST - LIFECYCLE FLOW'

        return 'Bạn bổ sung thêm thông tin còn thiếu giúp mình nhé.'

    @api.model
    def _build_agent_preview_by_intent(self, intent, fields_map, context_payload=None):
        if intent == 'create_project':
            return self._build_project_create_preview(fields_map)
        if intent == 'create_task':
            return self._build_task_create_preview(fields_map, context_payload or {})
        return self._build_document_create_preview(fields_map, context_payload or {})

    @api.model
    def _detect_agent_intent(self, user_message):
        raw = (user_message or '').strip().lower()
        text = self._normalize_text(user_message or '')
        create_markers = any(token in raw for token in [
            'tạo', 'tao', 'create', 'thêm', 'them', 'lập', 'lap', 'new', 'mới', 'moi'
        ])

        mentions_project = any(token in raw for token in ['project', 'dự án', 'du an'])
        mentions_task = any(token in raw for token in ['task', 'công việc', 'cong viec'])
        mentions_document = any(token in raw for token in ['document', 'tài liệu', 'tai lieu'])

        if create_markers and mentions_project and not mentions_task and not mentions_document:
            return 'create_project'

        if create_markers and mentions_task:
            return 'create_task'

        if create_markers and mentions_document:
            return 'create_document'

        # Fallback for loose phrases where terminal/browser encoding drops accents.
        if ('task' in text and 'project' in text) and ('deadline' in text or 'giao cho' in text or 'han' in text):
            return 'create_task'

        if ('document' in text and 'project' in text) or ('tai lieu' in text and 'project' in text):
            return 'create_document'

        if ('project' in text and 'team' in text) and ('task' not in text and 'document' not in text):
            return 'create_project'
        return False

    @api.model
    def _resolve_project_for_agent(self, fields_map, context_payload):
        project_name = (fields_map.get('project') or '').strip()
        if project_name:
            matches = self.env['project.nifty'].search([('name', 'ilike', project_name)], limit=2)
            if not matches:
                return False, 'Không tìm thấy project theo tên: %s' % project_name
            if len(matches) > 1:
                return False, 'Có nhiều project khớp "%s", vui lòng ghi rõ hơn.' % project_name
            return matches[0], ''

        context_project = self._resolve_active_project(context_payload or {})
        if context_project:
            return context_project, ''
        return False, 'Thiếu thông tin project. Hãy bổ sung `project: <tên project>`.'

    @api.model
    def _build_project_create_preview(self, fields_map):
        name = (fields_map.get('name') or '').strip()
        team_name = (fields_map.get('team') or '').strip()
        if not name:
            return False, 'Thiếu tên project. Ví dụ: `name: Triển khai CRM Q2`', False
        if not team_name:
            return False, 'Thiếu team. Ví dụ: `team: Nhóm Công nghệ A`', False

        team_matches = self.env['project.nifty.team'].search([('name', 'ilike', team_name)], limit=2)
        if not team_matches:
            return False, 'Không tìm thấy team theo tên: %s' % team_name, False
        if len(team_matches) > 1:
            return False, 'Có nhiều team khớp "%s", vui lòng ghi rõ hơn.' % team_name, False

        team = team_matches[0]
        payload = {
            'action': 'create_project',
            'name': name,
            'description': (fields_map.get('description') or '').strip(),
            'team_id': team.id,
            'team_name': team.name,
        }
        preview = [
            'Agent đã chuẩn bị 1 hành động:',
            '- Tạo Project: %s' % name,
            '- Team: %s' % team.name,
        ]
        if payload['description']:
            preview.append('- Mô tả: %s' % payload['description'])
        preview.append('Nhắn `xác nhận` để tạo, hoặc `hủy` để bỏ.')
        return payload, '\n'.join(preview), False

    @api.model
    def _build_task_create_preview(self, fields_map, context_payload):
        task_name = (fields_map.get('name') or '').strip()
        if not task_name:
            return False, 'Thiếu tên task. Ví dụ: `name: Thiết kế giao diện dashboard`', False

        project, err = self._resolve_project_for_agent(fields_map, context_payload)
        if not project:
            return False, err, False

        tasklist_name = (fields_map.get('tasklist') or '').strip()
        tasklist = False
        if tasklist_name:
            tasklist_matches = self.env['project.nifty.tasklist'].search([
                ('project_id', '=', project.id),
                ('name', 'ilike', tasklist_name),
            ], limit=2)
            if not tasklist_matches:
                return False, 'Không tìm thấy task list "%s" trong project %s.' % (tasklist_name, project.name), False
            if len(tasklist_matches) > 1:
                return False, 'Có nhiều task list khớp "%s", vui lòng ghi rõ hơn.' % tasklist_name, False
            tasklist = tasklist_matches[0]
        else:
            tasklist = self.env['project.nifty.tasklist'].search([('project_id', '=', project.id)], order='sequence,id', limit=1)
            if not tasklist:
                return False, 'Project %s chưa có task list. Hãy tạo task list trước.' % project.name, False

        deadline = (fields_map.get('deadline') or '').strip()
        if deadline:
            try:
                fields.Date.from_string(deadline)
            except Exception:
                return False, 'Định dạng deadline không hợp lệ. Dùng YYYY-MM-DD, ví dụ 2026-03-30.', False

        priority_value = (fields_map.get('priority') or '').strip().lower()
        priority_map = {
            'low': '0', 'thap': '0',
            'medium': '1', 'trung binh': '1',
            'high': '2', 'cao': '2',
            'critical': '3', 'khan': '3',
        }
        priority = priority_map.get(self._normalize_text(priority_value), '1') if priority_value else '1'

        assignee_ids = []
        assignees_raw = (fields_map.get('assignees') or '').strip()
        if assignees_raw:
            names = [n.strip() for n in assignees_raw.split(',') if n.strip()]
            for name in names:
                emp = self.env['nhan_vien'].search([
                    ('id', 'in', project.member_ids.ids),
                    '|',
                    ('ho_va_ten', 'ilike', name),
                    ('ma_dinh_danh', 'ilike', name),
                ], limit=1)
                if not emp:
                    return False, 'Không tìm thấy thành viên "%s" trong project %s.' % (name, project.name), False
                assignee_ids.append(emp.id)

        payload = {
            'action': 'create_task',
            'name': task_name,
            'description': (fields_map.get('description') or '').strip(),
            'project_id': project.id,
            'project_name': project.name,
            'tasklist_id': tasklist.id,
            'tasklist_name': tasklist.name,
            'deadline': deadline,
            'priority': priority,
            'assignee_ids': assignee_ids,
        }
        preview = [
            'Agent đã chuẩn bị 1 hành động:',
            '- Tạo Task: %s' % task_name,
            '- Project: %s' % project.name,
            '- Task list: %s' % tasklist.name,
            '- Priority: %s' % priority,
        ]
        if deadline:
            preview.append('- Deadline: %s' % deadline)
        if assignee_ids:
            assignees = self.env['nhan_vien'].browse(assignee_ids).mapped('ho_va_ten')
            preview.append('- Giao cho: %s' % ', '.join(assignees))
        preview.append('Nhắn `xác nhận` để tạo, hoặc `hủy` để bỏ.')
        return payload, '\n'.join(preview), False

    @api.model
    def _build_document_create_preview(self, fields_map, context_payload):
        doc_name = (fields_map.get('name') or '').strip()
        if not doc_name:
            return False, 'Thiếu tên document. Ví dụ: `name: Biên bản kickoff`', False

        project, err = self._resolve_project_for_agent(fields_map, context_payload)
        if not project:
            return False, err, False

        payload = {
            'action': 'create_document',
            'name': doc_name,
            'project_id': project.id,
            'project_name': project.name,
            'content': (fields_map.get('content') or fields_map.get('description') or '').strip(),
        }
        preview = [
            'Agent đã chuẩn bị 1 hành động:',
            '- Tạo Document: %s' % doc_name,
            '- Project: %s' % project.name,
        ]
        if payload['content']:
            preview.append('- Nội dung: đã có')
        preview.append('Nhắn `xác nhận` để tạo, hoặc `hủy` để bỏ.')
        return payload, '\n'.join(preview), False

    @api.model
    def _prepare_agent_action(self, user_message, context_payload=None):
        intent = self._detect_agent_intent(user_message)
        if not intent:
            return None

        fields_map = self._parse_agent_fields(user_message)
        natural_fields = self._parse_agent_fields_natural(user_message, intent)
        fields_map = self._merge_agent_fields(fields_map, natural_fields)

        missing_fields = self._missing_required_agent_fields(intent, fields_map, context_payload or {})
        if missing_fields:
            self._save_pending_action_state({
                'mode': 'collect_fields',
                'intent': intent,
                'fields_map': fields_map,
                'context_payload': context_payload or {},
                'missing_fields': missing_fields,
            })
            return {
                'reply': self._build_missing_fields_prompt(intent, missing_fields, fields_map),
                'provider': 'project_nifty_agent',
                'model': 'action_collect_fields',
                'pending_continuation': False,
            }

        payload, preview, _unused = self._build_agent_preview_by_intent(intent, fields_map, context_payload or {})

        if not payload:
            return {
                'reply': preview,
                'provider': 'project_nifty_agent',
                'model': 'action_preview',
                'pending_continuation': False,
            }

        self._save_pending_action_state(payload)
        return {
            'reply': preview,
            'provider': 'project_nifty_agent',
            'model': 'action_preview',
            'pending_continuation': False,
        }

    @api.model
    def _execute_pending_action(self, pending_action):
        action = (pending_action.get('action') or '').strip()
        if action == 'create_project':
            vals = {
                'name': pending_action.get('name') or '',
                'description': pending_action.get('description') or '',
                'team_id': pending_action.get('team_id'),
            }
            record = self.env['project.nifty'].create(vals)
            return 'Đã tạo Project thành công: %s (ID: %s)' % (record.name, record.id)

        if action == 'create_task':
            vals = {
                'name': pending_action.get('name') or '',
                'description': pending_action.get('description') or '',
                'project_id': pending_action.get('project_id'),
                'tasklist_id': pending_action.get('tasklist_id'),
                'priority': pending_action.get('priority') or '1',
            }
            if pending_action.get('deadline'):
                vals['deadline'] = pending_action.get('deadline')
            assignee_ids = pending_action.get('assignee_ids') or []
            if assignee_ids:
                vals['assigned_ids'] = [(6, 0, assignee_ids)]
            record = self.env['project.nifty.task'].create(vals)
            return 'Đã tạo Task thành công: %s (ID: %s)' % (record.name, record.id)

        if action == 'create_document':
            vals = {
                'name': pending_action.get('name') or '',
                'project_id': pending_action.get('project_id'),
                'content': pending_action.get('content') or '',
            }
            record = self.env['project.nifty.document'].create(vals)
            return 'Đã tạo Document thành công: %s (ID: %s)' % (record.name, record.id)

        raise ValidationError(_('Unsupported pending action.'))

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
        runtime_context = (pending_state.get('runtime_context') or '').strip()
        if not partial_reply:
            self._clear_pending_state()
            return {
                'reply': '',
                'model': self._google_model_name(),
                'provider': 'google',
                'pending_continuation': False,
            }

        continue_prompt = (
            'Bạn đang trả lời đang dở. Hãy tiếp tục ngay sau nội dung bên dưới, '
            'không lặp lại, không mở đầu lại, giữ cùng văn phong. '
            'Nếu cần số liệu thì ưu tiên bối cảnh dự án sau:\n\n%s\n\nNội dung đang dở:\n%s'
        ) % (runtime_context or 'Không có', partial_reply)
        content, finish_reason, model_name = self._google_generate(
            continue_prompt,
            self._int_param('project_nifty.ai_chat_continuation_tokens_google', 650, min_value=180, max_value=2000),
        )
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
        runtime_context = self._build_runtime_project_context(self.env.context.get('project_nifty_chat_context_payload') or {})
        prompt = self._build_google_prompt(user_message, history, runtime_context)
        content, finish_reason, model_name = self._google_generate(
            prompt,
            self._int_param('project_nifty.ai_chat_max_tokens_google', 1200, min_value=300, max_value=3000),
        )

        pending = bool(content) and finish_reason == 'MAX_TOKENS'
        if pending:
            self._save_pending_state({
                'provider': 'google',
                'model': model_name,
                'history': (history or [])[-10:],
                'user_message': user_message,
                'partial_reply': content,
                'runtime_context': runtime_context,
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
        runtime_context = (pending_state.get('runtime_context') or '').strip()
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
                    'content': self._build_system_prompt(runtime_context),
                },
                {
                    'role': 'user',
                    'content': (
                        'Hãy tiếp tục ngay sau nội dung đang dở sau đây, không lặp lại đoạn đã có:\n\n' + partial_reply
                    ),
                },
            ],
            'temperature': 0.4,
            'max_tokens': self._int_param('project_nifty.ai_chat_continuation_tokens_openai', 420, min_value=160, max_value=1800),
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
        runtime_context = self._build_runtime_project_context(self.env.context.get('project_nifty_chat_context_payload') or {})
        payload = {
            'model': self._openai_model_name(),
            'messages': self._build_chat_messages(user_message, history, runtime_context),
            'temperature': 0.5,
            'max_tokens': self._int_param('project_nifty.ai_chat_max_tokens_openai', 900, min_value=250, max_value=3000),
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
                'runtime_context': runtime_context,
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
        runtime_context = self._build_runtime_project_context(self.env.context.get('project_nifty_chat_context_payload') or {})
        history = history or []
        history = [line for line in history if isinstance(line, dict)]
        history = history[-6:]

        lines = [
            'You are Project Nifty Assistant.',
            'Reply in concise Vietnamese, practical and polite.',
            'If user asks about project management, tasks, documents, files, team workflow, give actionable guidance.',
            'Use available module context if provided below to answer closer to real project data.',
            '',
            'System context:',
            runtime_context or 'No context',
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
                'reply': _('Không có nội dung để phân tích.'),
                'provider': provider,
                'error': True,
            }

        try:
            if provider in ('google', 'gemini', 'google_ai_studio'):
                model_name = self._google_model_name()
                prompt_text = self._build_google_prompt(message, [])
                chunks = []

                content, finish_reason, model_name = self._google_generate(prompt_text, 700)
                if content:
                    chunks.append(content)

                tries = 0
                while finish_reason == 'MAX_TOKENS' and tries < 2 and chunks:
                    tries += 1
                    continue_prompt = (
                        'Hãy tiếp tục liền mạch ngay sau đoạn sau, không lặp lại, không mở đầu lại:\n\n'
                        + '\n'.join(chunks)
                    )
                    content, finish_reason, model_name = self._google_generate(continue_prompt, 320)
                    if content:
                        chunks.append(content)

                return {
                    'reply': ('\n'.join(chunks)).strip() or _('AI không trả về nội dung.'),
                    'provider': 'google',
                    'model': model_name,
                    'pending_continuation': False,
                }

            if provider == 'gpt2':
                result = self._call_gpt2_chat(message, [])
                result['pending_continuation'] = False
                return result

            api_key = self._openai_api_key()
            if not api_key:
                raise ValidationError(_(
                    'OpenAI API key is missing. Set system parameter project_nifty.openai_api_key or env OPENAI_API_KEY.'
                ))

            base_url = self._openai_base_url().rstrip('/')
            endpoint = base_url if base_url.endswith('/chat/completions') else f'{base_url}/chat/completions'

            def _openai_once(user_content, max_tokens):
                payload = {
                    'model': self._openai_model_name(),
                    'messages': [
                        {
                            'role': 'system',
                            'content': (
                                'Bạn là trợ lý AI cho module Project Nifty. '
                                'Trả lời bằng tiếng Việt, ngắn gọn, lịch sự, tập trung vào hướng dẫn thực tế cho quản lý dự án.'
                            ),
                        },
                        {'role': 'user', 'content': user_content},
                    ],
                    'temperature': 0.4,
                    'max_tokens': max_tokens,
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

                with urllib_request.urlopen(req, timeout=35) as response:
                    body = response.read().decode('utf-8')
                parsed = json.loads(body)
                choice = ((parsed.get('choices') or [{}])[0])
                content = (((choice.get('message') or {}).get('content')) or '').strip()
                finish_reason = (choice.get('finish_reason') or '').strip().lower()
                return content, finish_reason

            chunks = []
            content, finish_reason = _openai_once(message, 420)
            if content:
                chunks.append(content)

            tries = 0
            while finish_reason in ('length', 'max_tokens') and tries < 2 and chunks:
                tries += 1
                continue_prompt = (
                    'Hãy tiếp tục liền mạch ngay sau đoạn sau, không lặp lại, không mở đầu lại:\n\n'
                    + '\n'.join(chunks)
                )
                content, finish_reason = _openai_once(continue_prompt, 220)
                if content:
                    chunks.append(content)

            # Business suggestions should not interfere with chat continuation state.
            self._clear_pending_state()
            return {
                'reply': ('\n'.join(chunks)).strip() or _('AI không trả về nội dung.'),
                'provider': 'openai',
                'model': self._openai_model_name(),
                'pending_continuation': False,
            }
        except ValidationError as exc:
            return {
                'reply': _('Lỗi cấu hình AI: %s') % str(exc),
                'provider': provider,
                'error': True,
            }
        except Exception as exc:
            return {
                'reply': _('Không thể tạo gợi ý AI: %s') % str(exc),
                'provider': provider,
                'error': True,
            }

    @api.model
    def quick_support_reply(self, message, history=None, continue_last=False, context_payload=None):
        provider = self._ai_provider()
        try:
            if continue_last:
                return self._continue_pending_reply()

            user_message = (message or '').strip()
            if not user_message:
                return {
                    'reply': _('Vui lòng nhập nội dung trước khi gửi.'),
                    'provider': provider,
                    'error': True,
                    'pending_continuation': False,
                }
            history = history or []
            history = [line for line in history if isinstance(line, dict)]

            pending_action = self._load_pending_action_state()
            if pending_action:
                pending_mode = (pending_action.get('mode') or '').strip()

                if pending_mode == 'collect_fields':
                    if self._is_cancel_message(user_message):
                        self._clear_pending_action_state()
                        return {
                            'reply': 'Đã hủy hành động đang nhập dở.',
                            'provider': 'project_nifty_agent',
                            'pending_continuation': False,
                        }

                    if self._is_confirm_message(user_message):
                        missing_fields = pending_action.get('missing_fields') or []
                        return {
                            'reply': self._build_missing_fields_prompt(
                                pending_action.get('intent'),
                                missing_fields,
                                pending_action.get('fields_map') or {},
                            ),
                            'provider': 'project_nifty_agent',
                            'model': 'action_collect_fields',
                            'pending_continuation': False,
                        }

                    pending_intent = pending_action.get('intent')
                    pending_context = pending_action.get('context_payload') or (context_payload or {})
                    current_fields = pending_action.get('fields_map') or {}
                    typed_fields = self._parse_agent_fields(user_message)
                    natural_fields = self._parse_agent_fields_natural(user_message, pending_intent)
                    incoming_fields = self._merge_agent_fields(typed_fields, natural_fields)
                    merged_fields = self._merge_agent_fields_override(current_fields, incoming_fields)

                    missing_fields = self._missing_required_agent_fields(
                        pending_intent,
                        merged_fields,
                        pending_context,
                    )
                    if missing_fields:
                        self._save_pending_action_state({
                            'mode': 'collect_fields',
                            'intent': pending_intent,
                            'fields_map': merged_fields,
                            'context_payload': pending_context,
                            'missing_fields': missing_fields,
                        })
                        return {
                            'reply': self._build_missing_fields_prompt(pending_intent, missing_fields, merged_fields),
                            'provider': 'project_nifty_agent',
                            'model': 'action_collect_fields',
                            'pending_continuation': False,
                        }

                    payload, preview, _unused = self._build_agent_preview_by_intent(
                        pending_intent,
                        merged_fields,
                        pending_context,
                    )
                    if not payload:
                        # Keep collection mode so user can correct invalid values (e.g., unknown team/project).
                        self._save_pending_action_state({
                            'mode': 'collect_fields',
                            'intent': pending_intent,
                            'fields_map': merged_fields,
                            'context_payload': pending_context,
                            'missing_fields': self._missing_required_agent_fields(pending_intent, merged_fields, pending_context),
                        })
                        return {
                            'reply': preview,
                            'provider': 'project_nifty_agent',
                            'model': 'action_collect_fields',
                            'pending_continuation': False,
                        }

                    self._save_pending_action_state(payload)
                    return {
                        'reply': preview,
                        'provider': 'project_nifty_agent',
                        'model': 'action_preview',
                        'pending_continuation': False,
                    }

                if self._is_confirm_message(user_message):
                    try:
                        reply = self._execute_pending_action(pending_action)
                        self._clear_pending_action_state()
                        return {
                            'reply': reply,
                            'provider': 'project_nifty_agent',
                            'model': 'action_execute',
                            'pending_continuation': False,
                        }
                    except Exception as exc:
                        self._clear_pending_action_state()
                        return {
                            'reply': 'Không thể thực thi hành động: %s' % str(exc),
                            'provider': 'project_nifty_agent',
                            'error': True,
                            'pending_continuation': False,
                        }

                if self._is_cancel_message(user_message):
                    self._clear_pending_action_state()
                    return {
                        'reply': 'Đã hủy hành động đang chờ.',
                        'provider': 'project_nifty_agent',
                        'pending_continuation': False,
                    }

                return {
                    'reply': 'Bạn đang có 1 hành động chờ xác nhận. Nhắn `xác nhận` để thực thi hoặc `hủy` để bỏ.',
                    'provider': 'project_nifty_agent',
                    'pending_continuation': False,
                }

            agent_preview = self._prepare_agent_action(user_message, context_payload=context_payload or {})
            if agent_preview:
                self._clear_pending_state()
                return agent_preview

            deterministic = self._deterministic_support_reply(user_message, context_payload=context_payload or {})
            if deterministic:
                self._clear_pending_state()
                return deterministic

            service = self.with_context(project_nifty_chat_context_payload=(context_payload or {}))

            if provider in ('google', 'gemini', 'google_ai_studio'):
                return service._call_google_chat(user_message, history)
            if provider == 'gpt2':
                return service._call_gpt2_chat(user_message, history)
            return service._call_openai_chat(user_message, history)
        except ValidationError as exc:
            return {
                'reply': _('Lỗi cấu hình AI: %s') % str(exc),
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
        self._clear_pending_action_state()
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