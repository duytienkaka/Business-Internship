from datetime import date, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError


class ProjectNifty(models.Model):
    _name = 'project.nifty'
    _description = 'Enterprise Project'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Project Name', required=True, tracking=True)
    color = fields.Integer('Color', default=0)
    description = fields.Text('Description')
    team_id = fields.Many2one('project.nifty.team', string='Team')
    department_manager_id = fields.Many2one(
        'nhan_vien',
        string='Department Manager',
        required=True,
        tracking=True,
        default=lambda self: self._default_department_manager_id(),
    )
    manager_id = fields.Many2one('nhan_vien', string='Team Leader', required=True, tracking=True)
    member_ids = fields.Many2many(
        'nhan_vien',
        'project_nifty_member_nv_rel',
        'project_id',
        'nhan_vien_id',
        string='Project Members',
    )
    start_date = fields.Date('Start Date')
    end_date = fields.Date('End Date')
    status = fields.Selection([
        ('not_started', 'Not Started'),
        ('in_progress', 'In Progress'),
        ('pending_approval', 'Pending Approval'),
        ('finished', 'Finished'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='in_progress', tracking=True)

    progress = fields.Float('Progress (%)', compute='_compute_progress', store=True)

    is_department_manager_user = fields.Boolean('Is Department Manager', compute='_compute_role_flags')
    is_team_leader_user = fields.Boolean('Is Team Leader', compute='_compute_role_flags')
    is_member_user = fields.Boolean('Is Project Member', compute='_compute_role_flags')
    can_submit_completion_request = fields.Boolean('Can Submit Completion', compute='_compute_role_flags')

    total_task_count = fields.Integer('Total Tasks', compute='_compute_dashboard', store=True)
    tasklist_count = fields.Integer('Task List Count', compute='_compute_dashboard', store=True)
    approved_task_count = fields.Integer('Approved Tasks', compute='_compute_dashboard', store=True)
    pending_task_count = fields.Integer('Pending Tasks', compute='_compute_dashboard', store=True)
    rejected_task_count = fields.Integer('Rejected Tasks', compute='_compute_dashboard', store=True)
    document_count = fields.Integer('Documents Count', compute='_compute_dashboard', store=True)
    file_count = fields.Integer('Files Count', compute='_compute_dashboard', store=True)
    member_count = fields.Integer('Members', compute='_compute_dashboard', store=True)
    ai_feedback_total_count = fields.Integer('AI Feedback Total', compute='_compute_dashboard', store=True)
    ai_feedback_accepted_count = fields.Integer('AI Accepted', compute='_compute_dashboard', store=True)
    ai_feedback_partial_count = fields.Integer('AI Partial', compute='_compute_dashboard', store=True)
    ai_feedback_override_count = fields.Integer('AI Override', compute='_compute_dashboard', store=True)
    ai_feedback_acceptance_rate = fields.Float('AI Acceptance Rate (%)', compute='_compute_dashboard', store=True)
    ai_feedback_week_total_count = fields.Integer('AI Weekly Feedback Total', compute='_compute_dashboard', store=True)
    ai_feedback_week_accepted_count = fields.Integer('AI Weekly Accepted', compute='_compute_dashboard', store=True)
    ai_feedback_week_override_count = fields.Integer('AI Weekly Override', compute='_compute_dashboard', store=True)
    ai_feedback_week_acceptance_rate = fields.Float('AI Weekly Acceptance Rate (%)', compute='_compute_dashboard', store=True)

    tasklist_ids = fields.One2many('project.nifty.tasklist', 'project_id', string='Task Lists')
    task_ids = fields.One2many('project.nifty.task', 'project_id', string='Tasks')
    document_ids = fields.One2many('project.nifty.document', 'project_id', string='Documents')
    file_ids = fields.One2many('project.nifty.file', 'project_id', string='Files')

    def _skip_role_checks(self):
        return self.env.su or self.env.context.get('install_mode')

    @api.model
    def _default_department_manager_id(self):
        employee = self._get_current_nhan_vien(create_if_missing=True)
        return employee.id if employee else False

    @api.model
    def _get_current_nhan_vien(self, create_if_missing=False):
        user = self.env.user
        NhanVien = self.env['nhan_vien']
        employee = False

        if user.id:
            employee = NhanVien.search([('user_id', '=', user.id)], limit=1)

        if not employee and user.email:
            employee = NhanVien.search([('email', '=', user.email)], limit=1)
            if employee and not employee.user_id:
                employee.user_id = user.id

        if not employee and create_if_missing:
            full_name = (user.name or 'User').split()
            ten = full_name[-1] if full_name else 'User'
            ho_ten_dem = ' '.join(full_name[:-1]) if len(full_name) > 1 else 'User'
            ma_dinh_danh = f'user_{user.id}'
            if NhanVien.search_count([('ma_dinh_danh', '=', ma_dinh_danh)]):
                ma_dinh_danh = f'user_{user.id}_{int(fields.Datetime.now().timestamp())}'
            employee = NhanVien.create({
                'ma_dinh_danh': ma_dinh_danh,
                'ho_ten_dem': ho_ten_dem,
                'ten': ten,
                'email': user.email,
                'user_id': user.id,
                'ngay_sinh': date(1990, 1, 1),
            })

        return employee

    def _current_is_department_manager(self):
        self.ensure_one()
        employee = self._get_current_nhan_vien()
        return bool(employee and self.department_manager_id == employee)

    def _current_is_team_leader(self):
        self.ensure_one()
        employee = self._get_current_nhan_vien()
        return bool(employee and self.manager_id == employee)

    def _current_is_project_member(self):
        self.ensure_one()
        employee = self._get_current_nhan_vien()
        return bool(employee and employee in self.member_ids)

    def _current_can_access(self):
        self.ensure_one()
        return self._current_is_department_manager() or self._current_is_team_leader() or self._current_is_project_member()

    @api.depends('task_ids.status')
    def _compute_progress(self):
        for project in self:
            total = len(project.task_ids)
            approved = len(project.task_ids.filtered(lambda t: t.status == 'approved'))
            project.progress = (approved / total * 100) if total else 0.0

    def _compute_role_flags(self):
        employee = self._get_current_nhan_vien()
        for project in self:
            is_department_manager = bool(employee and project.department_manager_id == employee)
            is_team_leader = bool(employee and project.manager_id == employee)
            is_member = bool(employee and employee in project.member_ids)
            project.is_department_manager_user = is_department_manager
            project.is_team_leader_user = is_team_leader
            project.is_member_user = is_member
            project.can_submit_completion_request = is_team_leader and project.status in ('in_progress', 'not_started')

    @api.depends(
        'task_ids.status',
        'tasklist_ids',
        'document_ids',
        'file_ids',
        'member_ids',
        'task_ids.ai_feedback_state',
        'task_ids.ai_feedback_updated_at',
    )
    def _compute_dashboard(self):
        week_start = fields.Datetime.now() - timedelta(days=7)
        for project in self:
            total = len(project.task_ids)
            approved = len(project.task_ids.filtered(lambda t: t.status == 'approved'))
            rejected = len(project.task_ids.filtered(lambda t: t.status == 'rejected'))
            pending = total - approved

            feedback_tasks = project.task_ids.filtered(lambda t: t.ai_feedback_state in ('accepted', 'partial', 'manual_override'))
            accepted_feedback = len(feedback_tasks.filtered(lambda t: t.ai_feedback_state == 'accepted'))
            partial_feedback = len(feedback_tasks.filtered(lambda t: t.ai_feedback_state == 'partial'))
            override_feedback = len(feedback_tasks.filtered(lambda t: t.ai_feedback_state == 'manual_override'))
            feedback_total = accepted_feedback + partial_feedback + override_feedback
            acceptance_rate = ((accepted_feedback + partial_feedback) / feedback_total * 100.0) if feedback_total else 0.0

            weekly_feedback = feedback_tasks.filtered(lambda t: t.ai_feedback_updated_at and t.ai_feedback_updated_at >= week_start)
            weekly_accepted = len(weekly_feedback.filtered(lambda t: t.ai_feedback_state == 'accepted'))
            weekly_partial = len(weekly_feedback.filtered(lambda t: t.ai_feedback_state == 'partial'))
            weekly_override = len(weekly_feedback.filtered(lambda t: t.ai_feedback_state == 'manual_override'))
            weekly_total = weekly_accepted + weekly_partial + weekly_override
            weekly_acceptance_rate = ((weekly_accepted + weekly_partial) / weekly_total * 100.0) if weekly_total else 0.0

            project.total_task_count = total
            project.tasklist_count = len(project.tasklist_ids)
            project.approved_task_count = approved
            project.pending_task_count = pending
            project.rejected_task_count = rejected
            project.document_count = len(project.document_ids)
            project.file_count = len(project.file_ids)
            project.member_count = len(project.member_ids)
            project.ai_feedback_total_count = feedback_total
            project.ai_feedback_accepted_count = accepted_feedback
            project.ai_feedback_partial_count = partial_feedback
            project.ai_feedback_override_count = override_feedback
            project.ai_feedback_acceptance_rate = acceptance_rate
            project.ai_feedback_week_total_count = weekly_total
            project.ai_feedback_week_accepted_count = weekly_accepted + weekly_partial
            project.ai_feedback_week_override_count = weekly_override
            project.ai_feedback_week_acceptance_rate = weekly_acceptance_rate

    def _action_open_related(self, title, res_model, view_mode):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': res_model,
            'view_mode': view_mode,
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
        }

    @api.model_create_multi
    def create(self, vals_list):
        if self._skip_role_checks():
            return super().create(vals_list)
        if not self.env.user.has_group('project_nifty.group_project_nifty_department_manager'):
            raise AccessError(_('Only Department Manager can create projects.'))

        for vals in vals_list:
            if not vals.get('status'):
                vals['status'] = 'in_progress'

            if not vals.get('department_manager_id'):
                current_employee = self._get_current_nhan_vien(create_if_missing=True)
                vals['department_manager_id'] = current_employee.id if current_employee else False
            if not vals.get('department_manager_id'):
                raise ValidationError(_('Khong the tu dong gan Department Manager. Vui long lien he Admin de kiem tra du lieu nhan_vien.'))

            team = False
            team_id = vals.get('team_id')
            if team_id:
                team = self.env['project.nifty.team'].browse(team_id)

            if not team:
                raise ValidationError(_('Team is required.'))

            vals['manager_id'] = team.leader_id.id
            vals['member_ids'] = [(6, 0, team.member_ids.ids)]

            if not vals.get('manager_id'):
                raise ValidationError(_('Selected team does not have a Team Leader.'))

        return super().create(vals_list)

    def write(self, vals):
        if self._skip_role_checks():
            return super().write(vals)

        for rec in self:
            if not rec._current_can_access():
                raise AccessError(_('You can only access projects where you are Department Manager, Team Leader, or Project Member.'))

            if rec._current_is_department_manager():
                continue

            only_submit_completion = (
                self.env.context.get('allow_team_leader_submit_completion')
                and rec._current_is_team_leader()
                and set(vals.keys()) == {'status'}
                and vals.get('status') == 'pending_approval'
            )
            if not only_submit_completion:
                raise AccessError(_('Only Department Manager can edit project information.'))

        if vals.get('team_id'):
            team = self.env['project.nifty.team'].browse(vals['team_id'])
            vals['manager_id'] = team.leader_id.id
            vals['member_ids'] = [(6, 0, team.member_ids.ids)]

        return super().write(vals)

    @api.onchange('team_id')
    def _onchange_team_id(self):
        for rec in self:
            if not rec.team_id:
                continue
            rec.manager_id = rec.team_id.leader_id
            rec.member_ids = rec.team_id.member_ids
            if rec._origin and rec._origin.id and rec._origin.team_id and rec._origin.team_id != rec.team_id:
                return {
                    'warning': {
                        'title': _('Team changed'),
                        'message': _('Changing team will overwrite Team Leader and Project Members using the selected team.'),
                    }
                }

    def unlink(self):
        if self._skip_role_checks():
            return super().unlink()
        for rec in self:
            if not rec._current_is_department_manager():
                raise AccessError(_('Only Department Manager can delete projects.'))
        return super().unlink()

    def action_open_task_board(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Task Board',
            'res_model': 'project.nifty.task',
            'view_mode': 'kanban,tree,form',
            'domain': [('project_id', '=', self.id)],
            'context': {
                'default_project_id': self.id,
                'search_default_group_by_tasklist': 1,
            },
        }

    def action_open_tasklists(self):
        return self._action_open_related(_('Task Lists'), 'project.nifty.tasklist', 'tree,kanban,form')

    def action_open_tasks(self):
        return self._action_open_related(_('Tasks'), 'project.nifty.task', 'kanban,tree,form')

    def action_open_documents(self):
        return self._action_open_related(_('Documents'), 'project.nifty.document', 'tree,form')

    def action_open_files(self):
        return self._action_open_related(_('Files'), 'project.nifty.file', 'tree,form')

    def action_open_ai_feedback_tasks(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Assignment Feedback'),
            'res_model': 'project.nifty.task',
            'view_mode': 'kanban,tree,form',
            'domain': [
                ('project_id', '=', self.id),
                ('ai_feedback_state', 'in', ('accepted', 'partial', 'manual_override')),
            ],
            'context': {
                'default_project_id': self.id,
                'search_default_group_by_feedback_state': 1,
            },
        }

    def action_submit_completion_request(self):
        for rec in self:
            if not rec._current_is_team_leader():
                raise AccessError(_('Only Team Leader can submit project completion request.'))
            if rec.status in ('finished', 'cancelled'):
                raise ValidationError(_('Cannot submit completion request for finished or cancelled project.'))
            if rec.task_ids and any(task.status != 'approved' for task in rec.task_ids):
                raise ValidationError(_('All tasks must be Approved before submitting project completion request.'))
            rec.sudo().with_context(allow_team_leader_submit_completion=True).write({'status': 'pending_approval'})
        return True

    def action_department_manager_finish(self):
        for rec in self:
            if not rec._current_is_department_manager():
                raise AccessError(_('Only Department Manager can finalize project as Finished.'))
            rec.write({'status': 'finished'})
        return True

    def action_department_manager_cancel(self):
        for rec in self:
            if not rec._current_is_department_manager():
                raise AccessError(_('Only Department Manager can cancel project.'))
            rec.write({'status': 'cancelled'})
        return True

    @api.constrains('department_manager_id', 'manager_id', 'member_ids')
    def _check_assignment_consistency(self):
        for rec in self:
            if not rec.department_manager_id:
                raise ValidationError(_('Department Manager is required.'))
            if not rec.manager_id:
                raise ValidationError(_('Team Leader is required.'))
