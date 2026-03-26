from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError

class ProjectNiftyDocument(models.Model):
    _name = 'project.nifty.document'
    _description = 'Tai lieu du an Nifty'

    name = fields.Char('Title', required=True)
    project_id = fields.Many2one('project.nifty', string='Project', required=True, ondelete='cascade')
    content = fields.Html('Content')

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
                raise ValidationError(_('Document must belong to a project.'))
            if not self._is_team_leader(project):
                raise AccessError(_('Only Team Leader can create documents.'))
        return super().create(vals_list)

    def write(self, vals):
        if self._skip_role_checks():
            return super().write(vals)
        for rec in self:
            if not self._is_team_leader(rec.project_id):
                raise AccessError(_('Only Team Leader can edit documents.'))
        return super().write(vals)

    def unlink(self):
        if self._skip_role_checks():
            return super().unlink()
        for rec in self:
            if not self._is_team_leader(rec.project_id):
                raise AccessError(_('Only Team Leader can delete documents.'))
        return super().unlink()
