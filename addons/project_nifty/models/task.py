import json
import logging
from datetime import timedelta
from html import escape

from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError


_logger = logging.getLogger(__name__)


class ProjectNiftyTask(models.Model):
    _name = 'project.nifty.task'
    _description = 'Enterprise Project Task'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, deadline, id'

    name = fields.Char('Task Title', required=True, tracking=True)
    description = fields.Text('Description')
    project_id = fields.Many2one('project.nifty', string='Project', required=True, ondelete='cascade')
    project_team_id = fields.Many2one(
        'project.nifty.team',
        related='project_id.team_id',
        store=True,
        readonly=True,
        string='Team',
    )
    project_member_ids = fields.Many2many(
        'nhan_vien',
        related='project_id.member_ids',
        readonly=True,
        string='Project Members',
    )
    project_color = fields.Integer(related='project_id.color', string='Project Color', readonly=True)
    tasklist_id = fields.Many2one('project.nifty.tasklist', string='Task List', required=True, ondelete='cascade')
    assigned_ids = fields.Many2many(
        'nhan_vien',
        'project_nifty_task_nv_rel',
        'task_id',
        'nhan_vien_id',
        domain="[('id', 'in', project_member_ids)]",
        string='Assignees',
    )
    status = fields.Selection([
        ('todo', 'Đang chuẩn bị'),
        ('in_progress', 'Đang làm'),
        ('ready_review', 'Chờ duyệt'),
        ('approved', 'Đã duyệt'),
        ('rejected', 'Bị từ chối'),
    ], string='Trạng thái', default='todo', tracking=True)
    deadline = fields.Date('Deadline')
    priority = fields.Selection([
        ('0', 'Low'),
        ('1', 'Medium'),
        ('2', 'High'),
        ('3', 'Critical'),
    ], string='Priority', default='1')
    is_team_leader_user = fields.Boolean('Is Team Leader', compute='_compute_role_flags')
    can_submit_review = fields.Boolean('Can Submit Review', compute='_compute_role_flags')
    is_overdue = fields.Boolean('Is Overdue', compute='_compute_is_overdue')
    ai_suggested_assignee_ids = fields.Many2many(
        'nhan_vien',
        'project_nifty_ai_task_assignee_rel',
        'task_id',
        'nhan_vien_id',
        string='AI Suggested Assignees',
        readonly=True,
        domain="[('id', 'in', project_member_ids)]",
    )
    ai_last_suggested_assignee_ids = fields.Many2many(
        'nhan_vien',
        'project_nifty_ai_task_last_suggested_rel',
        'task_id',
        'nhan_vien_id',
        string='Last AI Suggested Assignees',
        readonly=True,
    )
    ai_feedback_assignee_ids = fields.Many2many(
        'nhan_vien',
        'project_nifty_ai_task_feedback_assignee_rel',
        'task_id',
        'nhan_vien_id',
        string='AI Feedback Assignees',
        readonly=True,
    )
    ai_feedback_state = fields.Selection([
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('partial', 'Partial'),
        ('manual_override', 'Manual Override'),
        ('no_suggestion', 'No Suggestion'),
    ], string='AI Feedback State', readonly=True, default='pending')
    ai_feedback_updated_at = fields.Datetime('AI Feedback Updated At', readonly=True)
    ai_feedback_summary = fields.Text('AI Feedback Summary', readonly=True)
    ai_assignment_recommendation = fields.Text('AI Assignment Recommendation', readonly=True)
    ai_assignment_risk_warning = fields.Text('AI Assignment Risk Warning', readonly=True)
    ai_assignment_score_breakdown = fields.Text('AI Assignment Score Breakdown', readonly=True)
    ai_assignment_metrics_html = fields.Html('AI Assignment Metrics Table', readonly=True)
    deadline_reminder_sent_for = fields.Date('Deadline Reminder Sent For', readonly=True, copy=False)

    def _skip_role_checks(self):
        return self.env.su or self.env.context.get('install_mode')

    def _current_nhan_vien(self):
        return self.env['project.nifty']._get_current_nhan_vien()

    @api.depends('project_id', 'assigned_ids')
    def _compute_role_flags(self):
        employee = self._current_nhan_vien()
        for task in self:
            is_team_leader = bool(task.project_id and employee and task.project_id.manager_id == employee)
            is_assignee = bool(task.project_id and employee and employee in task.assigned_ids)
            task.is_team_leader_user = bool(is_team_leader)
            task.can_submit_review = bool(is_assignee and not is_team_leader)

    @api.depends('deadline', 'status')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for task in self:
            task.is_overdue = bool(task.deadline and task.deadline < today and task.status not in ('approved',))

    def _is_team_leader(self, project):
        employee = self._current_nhan_vien()
        return bool(employee and project.manager_id == employee)

    def _is_department_manager(self, project):
        employee = self._current_nhan_vien()
        return bool(employee and project.department_manager_id == employee)

    def _is_project_member(self, project):
        employee = self._current_nhan_vien()
        return bool(employee and employee in project.member_ids)

    def _is_assigned_member(self, task):
        employee = self._current_nhan_vien()
        return bool(employee and employee in task.assigned_ids)

    def _validate_self_assignment_commands(self, commands):
        employee = self._current_nhan_vien()
        if not employee:
            raise AccessError(_('Current user is not linked to an employee record.'))
        employee_id = employee.id
        allowed = True
        for command in commands:
            if not isinstance(command, (list, tuple)) or not command:
                allowed = False
                break
            operation = command[0]
            if operation in (4, 3):
                if len(command) < 2 or command[1] != employee_id:
                    allowed = False
                    break
            elif operation == 5:
                allowed = False
                break
            elif operation == 6:
                if len(command) < 3 or set(command[2]) - {employee_id}:
                    allowed = False
                    break
            elif operation == 0:
                allowed = False
                break
            else:
                allowed = False
                break
        if not allowed:
            raise AccessError(_('Team Members can only assign or unassign themselves from tasks.'))

    @api.model
    def _extract_keywords(self, text):
        value = (text or '').lower().replace('\n', ' ')
        tokens = [token.strip('.,:;!?()[]{}"\'') for token in value.split()]
        short_allowlist = {'ui', 'ux', 'qa', 'db'}
        stopwords = {
            'task', 'project', 'team', 'module', 'cho', 'cua', 'voi', 'nhung', 'nay',
            'trong', 'dang', 'duoc', 'theo', 'de', 'va', 'bang', 'can', 'la', 'mot',
            'toi', 'uu', 'su', 'dung', 'xay', 'dung', 'he', 'thong', 'nguoi', 'ly', 'do',
        }
        cleaned = {token for token in tokens if token and (len(token) >= 3 or token in short_allowlist)}
        return {token for token in cleaned if token not in stopwords}

    @api.model
    def _get_scoring_weights(self):
        params = self.env['ir.config_parameter'].sudo()

        def _int_param(key, default):
            value = params.get_param(key)
            try:
                return int(value)
            except Exception:
                return default

        return {
            'focus_per': _int_param('project_nifty.ai_weight_focus_per', 8),
            'focus_cap': _int_param('project_nifty.ai_weight_focus_cap', 24),
            'keyword_per': _int_param('project_nifty.ai_weight_keyword_per', 2),
            'keyword_cap': _int_param('project_nifty.ai_weight_keyword_cap', 8),
            'profile_mismatch_penalty': _int_param('project_nifty.ai_penalty_profile_mismatch', 4),
            'same_task_bonus': _int_param('project_nifty.ai_bonus_same_task', 2),
            'same_team_bonus': _int_param('project_nifty.ai_bonus_same_team', 2),
            'workload_light_bonus': _int_param('project_nifty.ai_bonus_workload_light', 4),
            'workload_medium_bonus': _int_param('project_nifty.ai_bonus_workload_medium', 1),
            'workload_high_penalty': _int_param('project_nifty.ai_penalty_workload_high', 2),
            'overdue_cap': _int_param('project_nifty.ai_penalty_overdue_cap', 2),
            'critical_penalty': _int_param('project_nifty.ai_penalty_critical', 1),
        }

    @api.model
    def _normalize_skill_tokens(self, keywords):
        alias_map = {
            'postgresql': 'postgres',
            'database': 'postgres',
            'sql': 'postgres',
            'query': 'postgres',
            'frontend': 'frontend',
            'front-end': 'frontend',
            'reactjs': 'react',
            'javascript': 'frontend',
            'ui': 'uiux',
            'ux': 'uiux',
            'design': 'uiux',
            'python': 'backend',
            'api': 'backend',
            'backend': 'backend',
            'testing': 'testing',
            'test': 'testing',
            'unit': 'testing',
            'unittest': 'testing',
            'qa': 'testing',
            'ops': 'ops',
            'operation': 'ops',
            'operations': 'ops',
            'monitor': 'ops',
            'monitoring': 'ops',
            'incident': 'ops',
            'alert': 'ops',
            'production': 'ops',
            'devops': 'ops',
            'sre': 'ops',
        }
        normalized = set()
        for token in keywords:
            normalized.add(alias_map.get(token, token))
        return normalized

    @api.model
    def _skill_focus_from_keywords(self, keywords):
        normalized = self._normalize_skill_tokens(keywords)
        focus_set = {'frontend', 'react', 'uiux', 'backend', 'postgres', 'testing', 'ops'}
        return normalized & focus_set

    def _employee_workload_stats(self, employee):
        self.ensure_one()
        Task = self.env['project.nifty.task']
        today = fields.Date.context_today(self)
        open_domain = [
            ('assigned_ids', 'in', [employee.id]),
            ('status', 'not in', ['approved']),
        ]
        current_id = self._origin.id if self._origin and self._origin.id else False
        if current_id:
            open_domain.append(('id', '!=', current_id))
        overdue_domain = open_domain + [('deadline', '!=', False), ('deadline', '<', today)]
        critical_domain = open_domain + [('priority', '=', '3')]

        open_count = Task.search_count(open_domain)
        overdue_count = Task.search_count(overdue_domain)
        critical_count = Task.search_count(critical_domain)
        return {
            'open_count': open_count,
            'overdue_count': overdue_count,
            'critical_count': critical_count,
        }

    def _feedback_adjustment(self, employee):
        self.ensure_one()
        Task = self.env['project.nifty.task']
        base_domain = [
            ('project_id', '=', self.project_id.id),
        ]
        current_id = self._origin.id if self._origin and self._origin.id else False
        if current_id:
            base_domain.append(('id', '!=', current_id))

        accepted_count = Task.search_count(base_domain + [
            ('ai_feedback_state', 'in', ['accepted', 'partial']),
            ('ai_feedback_assignee_ids', 'in', [employee.id]),
        ])
        overridden_count = Task.search_count(base_domain + [
            ('ai_feedback_state', '=', 'manual_override'),
            ('ai_last_suggested_assignee_ids', 'in', [employee.id]),
        ])

        bonus = min(4, accepted_count) - min(3, overridden_count)
        reasons = []
        if accepted_count:
            reasons.append('được chọn tốt trong %s task trước' % accepted_count)
        if overridden_count:
            reasons.append('từng bị override %s lần' % overridden_count)
        return bonus, reasons

    def _employee_delivery_stats(self, employee):
        self.ensure_one()
        Task = self.env['project.nifty.task']
        current_id = self._origin.id if self._origin and self._origin.id else False
        domain = [
            ('project_id', '=', self.project_id.id),
            ('assigned_ids', 'in', [employee.id]),
        ]
        if current_id:
            domain.append(('id', '!=', current_id))

        tasks = Task.search(domain)
        total_worked = len(tasks)

        approved = tasks.filtered(lambda t: t.status == 'approved')
        on_time = 0
        late = 0
        for task in approved:
            if not task.deadline:
                continue
            done_date = fields.Date.to_date(task.write_date) if task.write_date else False
            if done_date and done_date <= task.deadline:
                on_time += 1
            elif done_date and done_date > task.deadline:
                late += 1

        return {
            'total_worked': total_worked,
            'on_time': on_time,
            'late': late,
        }

    def _record_assignment_feedback(self, source='manual'):
        for rec in self:
            suggested = rec.ai_last_suggested_assignee_ids
            chosen = rec.assigned_ids

            if not suggested:
                rec.write({
                    'ai_feedback_state': 'no_suggestion',
                    'ai_feedback_assignee_ids': [(6, 0, chosen.ids)],
                    'ai_feedback_updated_at': fields.Datetime.now(),
                    'ai_feedback_summary': _('No AI baseline to compare for this assignment.'),
                })
                continue

            overlap = suggested & chosen
            if overlap and len(overlap) == len(suggested) and len(chosen) == len(suggested):
                state = 'accepted'
            elif overlap:
                state = 'partial'
            else:
                state = 'manual_override'

            summary = _(
                'Source: %(source)s | Suggested: %(suggested)s | Chosen: %(chosen)s | Matched: %(matched)s'
            ) % {
                'source': source,
                'suggested': ', '.join(suggested.mapped('ho_va_ten')) or '-',
                'chosen': ', '.join(chosen.mapped('ho_va_ten')) or '-',
                'matched': ', '.join(overlap.mapped('ho_va_ten')) or '-',
            }

            rec.write({
                'ai_feedback_state': state,
                'ai_feedback_assignee_ids': [(6, 0, chosen.ids)],
                'ai_feedback_updated_at': fields.Datetime.now(),
                'ai_feedback_summary': summary,
            })

    def _score_assignee_fit(self, employee, with_breakdown=False):
        self.ensure_one()
        weights = self._get_scoring_weights()
        target_text = ' '.join([
            self.name or '',
            self.description or '',
            self.project_id.description or '',
        ])
        target_keywords = self._extract_keywords(target_text)
        target_skill_focus = self._skill_focus_from_keywords(target_keywords)

        score = 0
        breakdown = {}
        reasons = []
        cert_names = employee.danh_sach_chung_chi_bang_cap_ids.mapped('chung_chi_bang_cap_id.ten_chung_chi_bang_cap')
        position_names = employee.lich_su_cong_tac_ids.mapped('chuc_vu_id.ten_chuc_vu')
        profile_text = ' '.join([x for x in cert_names + position_names if x]).lower()
        profile_keywords = self._extract_keywords(profile_text)
        profile_skill_focus = self._skill_focus_from_keywords(profile_keywords)

        matched_focus = sorted(target_skill_focus & profile_skill_focus)
        if matched_focus:
            focus_points = min(weights['focus_cap'], len(matched_focus) * weights['focus_per'])
            score += focus_points
            breakdown['skill_focus'] = focus_points
            reasons.append('khớp nhóm kỹ năng: %s' % ', '.join(matched_focus[:3]))
        elif target_skill_focus:
            mismatch_penalty = -abs(weights['profile_mismatch_penalty'])
            score += mismatch_penalty
            breakdown['skill_focus'] = mismatch_penalty
            reasons.append('chưa khớp nhóm kỹ năng chính của task')
        else:
            breakdown['skill_focus'] = 0

        matched_keywords = sorted([kw for kw in target_keywords if kw in profile_text])
        if matched_keywords:
            keyword_points = min(weights['keyword_cap'], len(matched_keywords) * weights['keyword_per'])
            score += keyword_points
            breakdown['keyword_match'] = keyword_points
            reasons.append('khớp keyword: %s' % ', '.join(matched_keywords[:4]))
        else:
            breakdown['keyword_match'] = 0

        if employee in self.assigned_ids:
            score += weights['same_task_bonus']
            breakdown['same_task_continuity'] = weights['same_task_bonus']
            reasons.append('đang theo task này, giảm chi phí handover')
        else:
            breakdown['same_task_continuity'] = 0

        if self.project_team_id and employee.project_team_id == self.project_team_id:
            score += weights['same_team_bonus']
            breakdown['same_team'] = weights['same_team_bonus']
            reasons.append('thuộc team của project')
        else:
            breakdown['same_team'] = 0

        workload = self._employee_workload_stats(employee)
        open_count = workload['open_count']
        overdue_count = workload['overdue_count']
        critical_count = workload['critical_count']

        workload_points = 0
        if open_count <= 1:
            workload_points += weights['workload_light_bonus']
            reasons.append('tải trọng nhẹ (%s task đang mở)' % open_count)
        elif open_count <= 3:
            workload_points += weights['workload_medium_bonus']
            reasons.append('tải trọng vừa (%s task đang mở)' % open_count)
        elif open_count >= 6:
            workload_points -= abs(weights['workload_high_penalty'])
            reasons.append('tải trọng cao (%s task đang mở)' % open_count)

        if overdue_count:
            workload_points -= min(abs(weights['overdue_cap']), overdue_count)
            reasons.append('có %s task quá hạn' % overdue_count)

        if critical_count >= 2:
            workload_points -= abs(weights['critical_penalty'])
            reasons.append('đang giữ %s task critical' % critical_count)

        score += workload_points
        breakdown['workload'] = workload_points

        feedback_bonus, feedback_reasons = self._feedback_adjustment(employee)
        score += feedback_bonus
        breakdown['feedback_history'] = feedback_bonus
        reasons.extend(feedback_reasons)

        if not reasons:
            reasons.append('ứng viên dự phòng')

        breakdown['total'] = score
        if with_breakdown:
            return score, reasons, workload, breakdown
        return score, reasons, workload

    def _build_assignee_prompt_lines(self, candidates, scored):
        lines = []
        for emp in candidates:
            score, reasons, workload, _breakdown = scored.get(
                emp.id,
                (0, [], {'open_count': 0, 'overdue_count': 0, 'critical_count': 0}, {'total': 0}),
            )
            cert_names = emp.danh_sach_chung_chi_bang_cap_ids.mapped('chung_chi_bang_cap_id.ten_chung_chi_bang_cap')
            lines.append(
                '- %s | score=%s | open=%s overdue=%s critical=%s | chứng chỉ=%s | lý do=%s'
                % (
                    emp.ho_va_ten,
                    score,
                    workload.get('open_count', 0),
                    workload.get('overdue_count', 0),
                    workload.get('critical_count', 0),
                    ', '.join(cert_names[:3]) or 'N/A',
                    '; '.join(reasons),
                )
            )
        return lines

    def _format_assignment_score_table(self, suggested, scored):
        headers = ['Member', 'Total', 'Focus', 'Keyword', 'Team', 'Continuity', 'Workload', 'Feedback']
        rows = []

        for emp in suggested:
            score, _reasons, _workload, breakdown = scored.get(
                emp.id,
                (0, [], {'open_count': 0, 'overdue_count': 0, 'critical_count': 0}, {'total': 0}),
            )
            rows.append([
                (emp.ho_va_ten or '-') + ' (' + (emp.user_id.login or emp.ma_dinh_danh or '-') + ')',
                str(score),
                str(breakdown.get('skill_focus', 0)),
                str(breakdown.get('keyword_match', 0)),
                str(breakdown.get('same_team', 0)),
                str(breakdown.get('same_task_continuity', 0)),
                str(breakdown.get('workload', 0)),
                str(breakdown.get('feedback_history', 0)),
            ])

        widths = [len(h) for h in headers]
        for row in rows:
            for idx, value in enumerate(row):
                widths[idx] = max(widths[idx], len(value))

        def _fmt_row(values):
            return ' | '.join(value.ljust(widths[idx]) for idx, value in enumerate(values))

        separator = '-+-'.join('-' * w for w in widths)
        output = [_fmt_row(headers), separator]
        output.extend(_fmt_row(row) for row in rows)
        output.append('')
        output.append('Note: Total = Focus + Keyword + Team + Continuity + Workload + Feedback')
        return '\n'.join(output)

    def _format_assignment_metrics_html(self, suggested, scored):
        rows_html = []
        for emp in suggested:
            score, _reasons, workload, breakdown = scored.get(
                emp.id,
                (0, [], {'open_count': 0, 'overdue_count': 0, 'critical_count': 0}, {'total': 0}),
            )
            delivery = self._employee_delivery_stats(emp)
            rows_html.append(
                '<tr>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '</tr>'
                % (
                    escape((emp.ho_va_ten or '-') + ' (' + (emp.user_id.login or emp.ma_dinh_danh or '-') + ')'),
                    delivery.get('total_worked', 0),
                    delivery.get('on_time', 0),
                    delivery.get('late', 0),
                    workload.get('open_count', 0),
                    workload.get('overdue_count', 0),
                    workload.get('critical_count', 0),
                    breakdown.get('skill_focus', 0),
                    breakdown.get('keyword_match', 0),
                    breakdown.get('same_team', 0),
                    breakdown.get('feedback_history', 0),
                    score,
                )
            )

        return (
            '<div class="table-responsive">'
            '<table class="table table-sm table-bordered">'
            '<thead>'
            '<tr>'
            '<th>Member</th>'
            '<th>Số task đã làm</th>'
            '<th>Hoàn thành đúng hạn</th>'
            '<th>Hoàn thành trễ hạn</th>'
            '<th>Task đang mở</th>'
            '<th>Task đang quá hạn</th>'
            '<th>Task critical đang giữ</th>'
            '<th>Điểm khớp skill</th>'
            '<th>Điểm keyword</th>'
            '<th>Điểm team fit</th>'
            '<th>Điểm feedback</th>'
            '<th>Tổng điểm</th>'
            '</tr>'
            '</thead>'
            '<tbody>%s</tbody>'
            '</table>'
            '</div>'
        ) % ''.join(rows_html)

    def _cleanup_ai_recommendation(self, raw_text):
        text = (raw_text or '').replace('\r', '').strip()
        if not text:
            return ''

        lines = [line.strip() for line in text.split('\n') if line.strip()]
        cleaned = []
        for line in lines:
            lowered = line.lower().strip(':').strip()
            if lowered in ('tiep tuc', 'tiếp tục', '*', '-', 'chao ban,', 'chào bạn,', 'xin chao,', 'xin chào,'):
                continue
            if cleaned and cleaned[-1] == line:
                continue
            cleaned.append(line)

        if not cleaned:
            return ''

        return '\n'.join(cleaned[:8])

    def _build_structured_recommendation(self, suggested, scored, warning_text):
        lines = []
        lines.append('ĐỀ XUẤT PHÂN CÔNG TASK')
        lines.append('')
        lines.append('1) Top ứng viên')
        for idx, emp in enumerate(suggested, start=1):
            score, _reasons, _workload, _breakdown = scored.get(
                emp.id,
                (0, [], {'open_count': 0, 'overdue_count': 0, 'critical_count': 0}, {'total': 0}),
            )
            role_hint = 'Owner chính' if idx == 1 else 'Phối hợp'
            lines.append('%s. %s (%s) | Tong diem: %s | Vai tro goi y: %s' % (
                idx,
                emp.ho_va_ten or '-',
                emp.user_id.login or emp.ma_dinh_danh or '-',
                score,
                role_hint,
            ))

        lines.append('')
        lines.append('2) Lý do chọn')
        for emp in suggested:
            _score, reasons, workload, breakdown = scored.get(
                emp.id,
                (0, [], {'open_count': 0, 'overdue_count': 0, 'critical_count': 0}, {'total': 0}),
            )
            lines.append(
                '- %s: skill=%s, keyword=%s, workload=%s, feedback=%s | open=%s, overdue=%s, critical=%s'
                % (
                    emp.user_id.login or emp.ma_dinh_danh or '-',
                    breakdown.get('skill_focus', 0),
                    breakdown.get('keyword_match', 0),
                    breakdown.get('workload', 0),
                    breakdown.get('feedback_history', 0),
                    workload.get('open_count', 0),
                    workload.get('overdue_count', 0),
                    workload.get('critical_count', 0),
                )
            )
            if reasons:
                lines.append('  Ghi chú: %s' % '; '.join(reasons[:3]))

        lines.append('')
        lines.append('3) Kế hoạch giao việc và kiểm soát')
        if len(suggested) >= 2:
            lines.append('- Người #1 phụ trách đầu việc chính; người #2 hỗ trợ review/kiểm thử.')
        else:
            lines.append('- Người điểm cao nhất làm owner task và cập nhật tiến độ mỗi ngày.')
        lines.append('- Đặt mốc check-in giữa kỳ để xử lý backlog sớm và điều chỉnh nếu cần.')
        if warning_text:
            lines.append('- Có cảnh báo tải trọng: cần review trước khi chốt phân công.')

        return '\n'.join(lines)

    def _build_ai_assignment_payload(self):
        self.ensure_one()
        if not self._is_team_leader(self.project_id):
            raise AccessError(_('Only Team Leader can generate AI assignment suggestions.'))

        candidates = self.project_member_ids
        if not candidates:
            raise ValidationError(_('No project members available for AI assignment.'))

        scored = {emp.id: self._score_assignee_fit(emp, with_breakdown=True) for emp in candidates}
        sorted_candidates = candidates.sorted(
            key=lambda emp: (scored[emp.id][0], emp.ho_va_ten or ''),
            reverse=True,
        )
        suggested = sorted_candidates[:3]

        prompt_lines = [
            'Bạn là trợ lý AI hỗ trợ giao task trong dự án.',
            'Hãy đề xuất những nhân viên phù hợp nhất cho task dưới đây và giải thích ngắn gọn, dễ đọc.',
            'Trả lời tối đa 6 dòng, KHÔNG markdown, KHÔNG tiêu đề lặp lại, KHÔNG từ "tiếp tục".',
            'Trả lời theo 3 phần rõ ràng:',
            '1) Người đề xuất (tối đa 3 người)',
            '2) Lý do theo skill + workload (dạng bullet ngắn)',
            '3) Cách chia task và cảnh báo rủi ro (1-2 dòng)',
            '',
            'Task: %s' % (self.name or ''),
            'Mô tả task: %s' % (self.description or 'Không có mô tả'),
            'Project: %s' % (self.project_id.name or ''),
            'Mức ưu tiên: %s' % (self.priority or '1'),
            'Danh sách ứng viên:',
        ] + self._build_assignee_prompt_lines(suggested, scored)

        warnings = []
        for emp in suggested:
            _score, _reasons, workload, _breakdown = scored.get(
                emp.id,
                (0, [], {'open_count': 0, 'overdue_count': 0, 'critical_count': 0}, {'total': 0}),
            )
            if workload.get('open_count', 0) >= 6 or workload.get('overdue_count', 0) >= 2:
                warnings.append(
                    '- %s: open=%s, overdue=%s, critical=%s'
                    % (
                        emp.ho_va_ten,
                        workload.get('open_count', 0),
                        workload.get('overdue_count', 0),
                        workload.get('critical_count', 0),
                    )
                )

        warning_text = ''
        if warnings:
            warning_text = 'Cảnh báo tải trọng, cần review trước khi Apply:\n' + '\n'.join(warnings)

        score_table_text = self._format_assignment_score_table(suggested, scored)
        metrics_html = self._format_assignment_metrics_html(suggested, scored)
        structured_text = self._build_structured_recommendation(suggested, scored, warning_text)
        return {
            'ai_suggested_assignee_ids': [(6, 0, suggested.ids)],
            'ai_last_suggested_assignee_ids': [(6, 0, suggested.ids)],
            'ai_feedback_state': 'pending',
            'ai_feedback_assignee_ids': [(5, 0, 0)],
            'ai_feedback_summary': False,
            'ai_assignment_recommendation': structured_text,
            'ai_assignment_risk_warning': warning_text,
            'ai_assignment_score_breakdown': score_table_text,
            'ai_assignment_metrics_html': metrics_html,
        }

    @api.model
    def cron_ai_assignment_benchmark_monitor(self):
        params = self.env['ir.config_parameter'].sudo()
        min_accuracy = int(params.get_param('project_nifty.ai_monitor_min_accuracy', '70') or '70')

        bench_tasks = self.search([('name', 'ilike', 'AI Bench [%')], order='id desc', limit=300)
        if not bench_tasks:
            summary = {
                'status': 'no_data',
                'message': 'No benchmark tasks found',
                'checked_tasks': 0,
            }
            params.set_param('project_nifty.ai_benchmark_last_summary', json.dumps(summary, ensure_ascii=True))
            return True

        Log = self.env['project.nifty.ai.benchmark.log'].sudo()

        expected_map = {
            'STRONG': {'mb2', 'mb4', 'mb5', 'mb6'},
            'MEDIUM': {'mb3', 'mb7', 'mb8', 'mb9', 'mb10'},
            'WEAK': {'mb1', 'mb11', 'mb12', 'mb13'},
        }
        hit = 0
        checked = 0
        project_stats = {}
        for task in bench_tasks:
            name_upper = (task.name or '').upper()
            expected_key = 'STRONG' if '[STRONG]' in name_upper else 'MEDIUM' if '[MEDIUM]' in name_upper else 'WEAK' if '[WEAK]' in name_upper else ''
            if not expected_key:
                continue

            suggested = set(task.ai_suggested_assignee_ids.mapped('user_id.login'))
            checked += 1
            project_key = task.project_id.id
            if project_key not in project_stats:
                project_stats[project_key] = {'hit': 0, 'checked': 0}
            project_stats[project_key]['checked'] += 1
            if suggested & expected_map[expected_key]:
                hit += 1
                project_stats[project_key]['hit'] += 1

        accuracy = round((hit * 100.0 / checked), 2) if checked else 0.0
        status = 'healthy' if accuracy >= min_accuracy else 'degraded'
        summary = {
            'status': status,
            'accuracy': accuracy,
            'min_accuracy': min_accuracy,
            'hit': hit,
            'checked_tasks': checked,
            'checked_at': fields.Datetime.now().isoformat(),
        }
        params.set_param('project_nifty.ai_benchmark_last_summary', json.dumps(summary, ensure_ascii=True))

        run_at = fields.Datetime.now()
        for project_id, stat in project_stats.items():
            proj_checked = stat['checked']
            proj_hit = stat['hit']
            proj_accuracy = round((proj_hit * 100.0 / proj_checked), 2) if proj_checked else 0.0
            proj_status = 'healthy' if proj_accuracy >= min_accuracy else 'degraded'
            Log.create({
                'name': 'AI Benchmark %s' % fields.Date.context_today(self),
                'run_at': run_at,
                'project_id': project_id,
                'status': proj_status,
                'accuracy': proj_accuracy,
                'min_accuracy': min_accuracy,
                'hit_count': proj_hit,
                'checked_count': proj_checked,
            })

        _logger.info('Project Nifty AI benchmark monitor summary: %s', summary)
        return True

    @api.model
    def cron_send_deadline_reminder_emails(self):
        today = fields.Date.context_today(self)
        target_deadline = today + timedelta(days=1)

        tasks = self.search([
            ('deadline', '=', target_deadline),
            ('status', 'not in', ['ready_review', 'approved']),
        ])
        self._send_deadline_reminders(tasks, mark_as_sent=True, skip_already_sent=True)

        return True

    def _send_deadline_reminders(self, tasks, mark_as_sent=True, skip_already_sent=True):
        if not tasks:
            return 0

        status_labels = dict(self._fields['status'].selection)
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        action = self.env.ref('project_nifty.action_project_nifty_task', raise_if_not_found=False)
        mail_obj = self.env['mail.mail'].sudo()
        sent_count = 0

        for task in tasks:
            if skip_already_sent and task.deadline_reminder_sent_for and task.deadline_reminder_sent_for == task.deadline:
                continue

            recipients = set()
            recipients.update(email for email in task.assigned_ids.mapped('email') if email)
            leader_email = task.project_id.manager_id.email
            if leader_email:
                recipients.add(leader_email)
            if not recipients:
                continue

            task_url = ''
            if base_url and action:
                task_url = '%s/web#id=%s&model=project.nifty.task&view_type=form&action=%s' % (
                    base_url,
                    task.id,
                    action.id,
                )

            email_values = {
                'email_to': ','.join(sorted(recipients)),
                'auto_delete': False,
            }

            subject = '[Project Nifty] Nhắc việc: Task "%s" sắp đến hạn' % (task.name or 'N/A')
            body_lines = [
                '<p>Xin chào,</p>',
                '<p>Task <strong>%s</strong> sẽ đến hạn sau 1 ngày và hiện chưa ở trạng thái xác nhận hoàn thành.</p>' % escape(task.name or ''),
                '<ul>',
                '<li><strong>Project:</strong> %s</li>' % escape(task.project_id.name or ''),
                '<li><strong>Task list:</strong> %s</li>' % escape(task.tasklist_id.name or ''),
                '<li><strong>Hạn chót:</strong> %s</li>' % (task.deadline or ''),
                '<li><strong>Trạng thái hiện tại:</strong> %s</li>' % escape(status_labels.get(task.status, task.status or '')),
                '<li><strong>Người được giao:</strong> %s</li>' % escape(', '.join(task.assigned_ids.mapped('ho_va_ten')) or 'Chưa có'),
                '<li><strong>Team Leader:</strong> %s</li>' % escape(task.project_id.manager_id.ho_va_ten or 'N/A'),
                '</ul>',
            ]
            if task_url:
                body_lines.append('<p><a href="%s">Mở task trong hệ thống</a></p>' % task_url)
            body_lines.append('<p>Vui lòng cập nhật tiến độ hoặc chuyển task sang trạng thái Ready for Review khi đã hoàn thành.</p>')

            mail_obj.create({
                'subject': subject,
                'body_html': ''.join(body_lines),
                'email_to': email_values['email_to'],
                'auto_delete': email_values['auto_delete'],
            }).send()

            sent_count += 1
            if mark_as_sent:
                task.sudo().write({'deadline_reminder_sent_for': task.deadline})

        return sent_count

    def action_send_deadline_reminder_now(self):
        for rec in self:
            if not rec._is_team_leader(rec.project_id) and not self.env.user.has_group('base.group_system'):
                raise AccessError(_('Only Team Leader or Admin can send deadline reminder emails.'))
            if rec.status in ('ready_review', 'approved'):
                raise ValidationError(_('Task is already submitted/completed; no reminder is needed.'))
            if not rec.deadline:
                raise ValidationError(_('Task must have a deadline before sending reminder email.'))

        sent_count = self._send_deadline_reminders(self, mark_as_sent=False, skip_already_sent=False)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Deadline reminder'),
                'message': _('Sent %s reminder email(s).') % sent_count,
                'type': 'success' if sent_count else 'warning',
                'sticky': False,
            }
        }

    @api.onchange('project_id', 'name', 'description', 'priority')
    def _onchange_ai_preview_for_new_task(self):
        for rec in self:
            if rec.id:
                continue
            if not rec.project_id or not (rec.name or '').strip():
                continue
            if not rec._is_team_leader(rec.project_id):
                continue
            try:
                rec.update(rec._build_ai_assignment_payload())
            except Exception:
                # Keep form responsive even if AI service is temporarily unavailable.
                rec.ai_assignment_recommendation = _('Không thể tạo gợi ý ngay lúc này. Bạn có thể bấm AI Suggest sau khi lưu task.')

    @api.model_create_multi
    def create(self, vals_list):
        if self._skip_role_checks():
            return super().create(vals_list)

        forced_tasklist_id = self.env.context.get('default_tasklist_id') if self.env.context.get('from_tasklist_form') else False
        forced_tasklist = self.env['project.nifty.tasklist'].browse(forced_tasklist_id) if forced_tasklist_id else False

        for vals in vals_list:
            if forced_tasklist:
                # Task created from Task List area must stay in that Task List.
                vals['tasklist_id'] = forced_tasklist.id
                vals['project_id'] = forced_tasklist.project_id.id

            if not vals.get('project_id') and vals.get('tasklist_id'):
                fallback_tasklist = self.env['project.nifty.tasklist'].browse(vals.get('tasklist_id'))
                if fallback_tasklist:
                    vals['project_id'] = fallback_tasklist.project_id.id

            project = self.env['project.nifty'].browse(vals.get('project_id'))
            if not project:
                raise ValidationError(_('A task must belong to a project.'))
            if not self._is_team_leader(project):
                raise AccessError(_('Only Team Leader can create tasks.'))
            tasklist_id = vals.get('tasklist_id')
            if tasklist_id:
                tasklist = self.env['project.nifty.tasklist'].browse(tasklist_id)
                if tasklist.project_id != project:
                    raise ValidationError(_('Task list must belong to the same project as the task.'))
            if vals.get('status') in ('approved', 'rejected'):
                raise ValidationError(_('New task cannot start in Approved/Rejected status.'))
        return super().create(vals_list)

    def write(self, vals):
        if self._skip_role_checks():
            return super().write(vals)
        member_allowed_fields = {'status'}
        track_manual_feedback = bool('assigned_ids' in vals and not self.env.context.get('skip_ai_feedback'))
        manual_feedback_task_ids = set()
        for rec in self:
            project = rec.project_id
            if self._is_team_leader(project):
                if track_manual_feedback:
                    manual_feedback_task_ids.add(rec.id)
                continue

            # Department Manager can view dashboard/project status, but does not manage task lifecycle.
            if self._is_department_manager(project):
                raise AccessError(_('Department Manager cannot directly edit tasks.'))

            if not self._is_project_member(project):
                raise AccessError(_('Only assigned project members can edit tasks.'))

            if not self._is_assigned_member(rec):
                raise AccessError(_('Team Member can only edit tasks assigned to themselves.'))

            if 'assigned_ids' in vals or 'tasklist_id' in vals or 'project_id' in vals:
                raise AccessError(_('Team Member cannot reassign tasks or move tasks between task lists.'))

            if set(vals.keys()) - member_allowed_fields:
                raise AccessError(_('Team Member cannot edit this task field.'))

            if 'status' in vals and vals['status'] not in ('in_progress', 'ready_review'):
                raise AccessError(_('Team Member can only set status to In Progress or Ready for Review.'))

        result = super().write(vals)
        if track_manual_feedback and manual_feedback_task_ids:
            to_feedback = self.filtered(lambda task: task.id in manual_feedback_task_ids)
            to_feedback.with_context(skip_ai_feedback=True)._record_assignment_feedback(source='manual')
        return result

    def unlink(self):
        if self._skip_role_checks():
            return super().unlink()
        for rec in self:
            if not self._is_team_leader(rec.project_id):
                raise AccessError(_('Only Team Leader can delete tasks.'))
        return super().unlink()

    def _send_task_approved_notification(self):
        mail_obj = self.env['mail.mail'].sudo()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        action = self.env.ref('project_nifty.action_project_nifty_tasks', raise_if_not_found=False)

        for rec in self:
            recipients = sorted({email for email in rec.assigned_ids.mapped('email') if email})
            if not recipients:
                continue

            task_url = ''
            if base_url and action:
                task_url = '%s/web#id=%s&model=project.nifty.task&view_type=form&action=%s' % (
                    base_url,
                    rec.id,
                    action.id,
                )

            leader_name = rec.project_id.manager_id.ho_va_ten or self.env.user.name or 'Team Leader'
            body_html = [
                '<p>Xin chào,</p>',
                '<p>Task <strong>%s</strong> đã được Team Leader <strong>%s</strong> xác nhận hoàn thành.</p>' % (
                    escape(rec.name or ''),
                    escape(leader_name),
                ),
                '<ul>',
                '<li><strong>Project:</strong> %s</li>' % escape(rec.project_id.name or ''),
                '<li><strong>Task list:</strong> %s</li>' % escape(rec.tasklist_id.name or ''),
                '<li><strong>Trạng thái mới:</strong> Approved</li>',
                '</ul>',
            ]
            if task_url:
                body_html.append('<p><a href="%s">Mở task trong hệ thống</a></p>' % task_url)

            mail_obj.create({
                'subject': '[Project Nifty] Task được phê duyệt: "%s"' % (rec.name or 'N/A'),
                'body_html': ''.join(body_html),
                'email_to': ','.join(recipients),
                'auto_delete': False,
            }).send()
        return True

    def action_submit_for_review(self):
        for rec in self:
            if not self._is_assigned_member(rec):
                raise AccessError(_('Only assigned Team Member can submit task for review.'))
            if self._is_team_leader(rec.project_id):
                raise AccessError(_('Team Leader should use Approve/Reject actions.'))
            rec.write({'status': 'ready_review'})
        return True

    def action_approve_task(self):
        for rec in self:
            if not self._is_team_leader(rec.project_id):
                raise AccessError(_('Only Team Leader can approve tasks.'))
            rec.write({'status': 'approved'})
            rec._send_task_approved_notification()
        return True

    def action_reject_task(self):
        for rec in self:
            if not self._is_team_leader(rec.project_id):
                raise AccessError(_('Only Team Leader can reject tasks.'))
            rec.write({'status': 'rejected'})
        return True

    def action_open_project_task_board(self):
        self.ensure_one()
        if not self.project_id:
            raise ValidationError(_('Task must belong to a project.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Project Task Board'),
            'res_model': 'project.nifty.task',
            'view_mode': 'kanban,tree,form',
            'domain': [('project_id', '=', self.project_id.id)],
            'context': {
                'default_project_id': self.project_id.id,
                'search_default_group_by_tasklist': 1,
            },
        }

    def action_ai_suggest_assignees(self):
        for rec in self:
            rec.write(rec._build_ai_assignment_payload())
        return True

    def action_ai_apply_suggested_assignees(self):
        for rec in self:
            if not rec._is_team_leader(rec.project_id):
                raise AccessError(_('Only Team Leader can apply AI assignment suggestions.'))
            if not rec.ai_suggested_assignee_ids:
                raise ValidationError(_('No AI suggestion available. Please run AI Suggest first.'))
            rec.with_context(skip_ai_feedback=True).write({'assigned_ids': [(6, 0, rec.ai_suggested_assignee_ids.ids)]})
            rec.with_context(skip_ai_feedback=True)._record_assignment_feedback(source='apply')
        return True

    @api.constrains('project_id', 'tasklist_id')
    def _check_project_tasklist_consistency(self):
        for task in self:
            if task.project_id and task.tasklist_id and task.tasklist_id.project_id != task.project_id:
                raise ValidationError(_('Task list must belong to the same project as the task.'))
