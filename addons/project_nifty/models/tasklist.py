from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError

class ProjectNiftyTaskList(models.Model):
    _name = 'project.nifty.tasklist'
    _description = 'Danh sach cong viec Nifty'
    _order = 'sequence, id'

    name = fields.Char('Task List Name', required=True)
    sequence = fields.Integer('Sequence', default=10)
    project_id = fields.Many2one('project.nifty', string='Project', required=True, ondelete='cascade')
    project_member_ids = fields.Many2many(
        'nhan_vien',
        related='project_id.member_ids',
        readonly=True,
        string='Project Members',
    )
    task_ids = fields.One2many('project.nifty.task', 'tasklist_id', string='Tasks')
    total_task_count = fields.Integer('Total Tasks', compute='_compute_progress_metrics', store=True)
    approved_task_count = fields.Integer('Approved Tasks', compute='_compute_progress_metrics', store=True)
    progress = fields.Float('Progress (%)', compute='_compute_progress_metrics', store=True)

    @api.depends('task_ids.status')
    def _compute_progress_metrics(self):
        for rec in self:
            total = len(rec.task_ids)
            approved = len(rec.task_ids.filtered(lambda t: t.status == 'approved'))
            rec.total_task_count = total
            rec.approved_task_count = approved
            rec.progress = (approved / total * 100.0) if total else 0.0

    def _skip_role_checks(self):
        return self.env.su or self.env.context.get('install_mode')

    def _is_team_leader(self, project):
        return project._current_is_team_leader()

    @api.model_create_multi
    def create(self, vals_list):
        if self._skip_role_checks():
            return super().create(vals_list)
        for vals in vals_list:
            project = self.env['project.nifty'].browse(vals.get('project_id'))
            if not project:
                raise ValidationError(_('Task list must belong to a project.'))
            if not self._is_team_leader(project):
                raise AccessError(_('Only Team Leader can create task lists.'))
        return super().create(vals_list)

    def write(self, vals):
        if self._skip_role_checks():
            return super().write(vals)
        for rec in self:
            if not self._is_team_leader(rec.project_id):
                raise AccessError(_('Only Team Leader can edit task lists.'))
        return super().write(vals)

    def unlink(self):
        if self._skip_role_checks():
            return super().unlink()
        for rec in self:
            if not self._is_team_leader(rec.project_id):
                raise AccessError(_('Only Team Leader can delete task lists.'))
        return super().unlink()
