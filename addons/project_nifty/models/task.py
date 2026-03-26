from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError


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
        ('todo', 'Todo'),
        ('in_progress', 'In Progress'),
        ('ready_review', 'Ready for Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string='Status', default='todo', tracking=True)
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

    def _skip_role_checks(self):
        return self.env.su or self.env.context.get('install_mode')

    def _current_nhan_vien(self):
        return self.env['project.nifty']._get_current_nhan_vien()

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
        return {token for token in tokens if len(token) >= 4}

    def _employee_workload_stats(self, employee):
        self.ensure_one()
        Task = self.env['project.nifty.task']
        today = fields.Date.context_today(self)
        open_domain = [
            ('assigned_ids', 'in', [employee.id]),
            ('status', 'not in', ['approved']),
            ('id', '!=', self.id),
        ]
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
            ('id', '!=', self.id),
        ]

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
            reasons.append('duoc chon tot trong %s task truoc' % accepted_count)
        if overridden_count:
            reasons.append('tung bi override %s lan' % overridden_count)
        return bonus, reasons

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

    def _score_assignee_fit(self, employee):
        self.ensure_one()
        target_text = ' '.join([
            self.name or '',
            self.description or '',
            self.project_id.description or '',
        ])
        target_keywords = self._extract_keywords(target_text)

        score = 0
        reasons = []
        cert_names = employee.danh_sach_chung_chi_bang_cap_ids.mapped('chung_chi_bang_cap_id.ten_chung_chi_bang_cap')
        position_names = employee.lich_su_cong_tac_ids.mapped('chuc_vu_id.ten_chuc_vu')
        profile_text = ' '.join([x for x in cert_names + position_names if x]).lower()

        matched_keywords = sorted([kw for kw in target_keywords if kw in profile_text])
        if matched_keywords:
            score += min(10, len(matched_keywords) * 2)
            reasons.append('khop keyword: %s' % ', '.join(matched_keywords[:4]))

        if employee in self.assigned_ids:
            score += 2
            reasons.append('dang theo task nay, giam chi phi handover')

        if self.project_team_id and employee.project_team_id == self.project_team_id:
            score += 2
            reasons.append('thuoc team cua project')

        workload = self._employee_workload_stats(employee)
        open_count = workload['open_count']
        overdue_count = workload['overdue_count']
        critical_count = workload['critical_count']

        if open_count <= 1:
            score += 4
            reasons.append('tai trong nhe (%s task dang mo)' % open_count)
        elif open_count <= 3:
            score += 1
            reasons.append('tai trong vua (%s task dang mo)' % open_count)
        elif open_count >= 6:
            score -= 4
            reasons.append('tai trong cao (%s task dang mo)' % open_count)

        if overdue_count:
            score -= min(4, overdue_count)
            reasons.append('co %s task qua han' % overdue_count)

        if critical_count >= 2:
            score -= 2
            reasons.append('dang giu %s task critical' % critical_count)

        feedback_bonus, feedback_reasons = self._feedback_adjustment(employee)
        score += feedback_bonus
        reasons.extend(feedback_reasons)

        if not reasons:
            reasons.append('ung vien du phong')

        return score, reasons, workload

    def _build_assignee_prompt_lines(self, candidates, scored):
        lines = []
        for emp in candidates:
            score, reasons, workload = scored.get(emp.id, (0, [], {'open_count': 0, 'overdue_count': 0, 'critical_count': 0}))
            cert_names = emp.danh_sach_chung_chi_bang_cap_ids.mapped('chung_chi_bang_cap_id.ten_chung_chi_bang_cap')
            lines.append(
                '- %s | score=%s | open=%s overdue=%s critical=%s | chung chi=%s | ly do=%s'
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
        member_allowed_fields = {'name', 'description', 'deadline', 'priority', 'status'}
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
            if not rec._is_team_leader(rec.project_id):
                raise AccessError(_('Only Team Leader can generate AI assignment suggestions.'))

            candidates = rec.project_member_ids
            if not candidates:
                raise ValidationError(_('No project members available for AI assignment.'))

            scored = {emp.id: rec._score_assignee_fit(emp) for emp in candidates}
            sorted_candidates = candidates.sorted(
                key=lambda emp: (scored[emp.id][0], emp.ho_va_ten or ''),
                reverse=True,
            )
            suggested = sorted_candidates[:3]

            prompt_lines = [
                'Ban la tro ly AI ho tro giao task trong du an.',
                'Hay de xuat nhung nhan vien phu hop nhat cho task duoi day va giai thich ngan gon.',
                'Tra loi theo 3 phan: (1) Nguoi de xuat (2) Ly do theo skill + workload (3) Cach chia task/kiem soat rui ro.',
                '',
                'Task: %s' % (rec.name or ''),
                'Mo ta task: %s' % (rec.description or 'Khong co mo ta'),
                'Project: %s' % (rec.project_id.name or ''),
                'Muc uu tien: %s' % (rec.priority or '1'),
                'Danh sach ung vien:',
            ] + rec._build_assignee_prompt_lines(suggested, scored)

            ai_result = self.env['project.nifty.ai.chat'].sudo().generate_advice_once('\n'.join(prompt_lines))
            warnings = []
            for emp in suggested:
                _score, _reasons, workload = scored.get(emp.id, (0, [], {'open_count': 0, 'overdue_count': 0, 'critical_count': 0}))
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
                warning_text = 'Canh bao tai trong, can review truoc khi Apply:\n' + '\n'.join(warnings)

            rec.write({
                'ai_suggested_assignee_ids': [(6, 0, suggested.ids)],
                'ai_last_suggested_assignee_ids': [(6, 0, suggested.ids)],
                'ai_feedback_state': 'pending',
                'ai_feedback_assignee_ids': [(5, 0, 0)],
                'ai_feedback_summary': False,
                'ai_assignment_recommendation': (ai_result.get('reply') or '').strip() or _('Khong nhan duoc noi dung de xuat tu AI.'),
                'ai_assignment_risk_warning': warning_text,
            })
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
