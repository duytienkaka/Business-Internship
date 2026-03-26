from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError

class ProjectNiftyFile(models.Model):
    _name = 'project.nifty.file'
    _description = 'Tep tin du an Nifty'

    name = fields.Char('File Name', required=True)
    project_id = fields.Many2one('project.nifty', string='Project', required=True, ondelete='cascade')
    uploaded_by = fields.Many2one('res.users', string='Uploaded By', required=True)
    upload_date = fields.Datetime('Upload Date', default=fields.Datetime.now)
    file = fields.Binary('File', required=True)

    def _skip_role_checks(self):
        return self.env.su or self.env.context.get('install_mode')

    def _is_team_leader(self, project):
        return project._current_is_team_leader()

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if not values.get('uploaded_by'):
            values['uploaded_by'] = self.env.user.id
        return values

    @api.onchange('project_id')
    def _onchange_project_id_set_uploaded_by(self):
        for rec in self:
            rec.uploaded_by = self.env.user

    @api.model_create_multi
    def create(self, vals_list):
        if self._skip_role_checks():
            for vals in vals_list:
                if not vals.get('uploaded_by'):
                    vals['uploaded_by'] = self.env.user.id
            return super().create(vals_list)
        for vals in vals_list:
            project = self.env['project.nifty'].browse(vals.get('project_id'))
            if not project:
                raise ValidationError(_('File must belong to a project.'))
            if not self._is_team_leader(project):
                raise AccessError(_('Only Team Leader can upload files.'))
            vals['uploaded_by'] = self.env.user.id
        return super().create(vals_list)

    def write(self, vals):
        if self._skip_role_checks():
            return super().write(vals)
        for rec in self:
            if not self._is_team_leader(rec.project_id):
                raise AccessError(_('Only Team Leader can edit files.'))
        vals['uploaded_by'] = self.env.user.id
        return super().write(vals)

    def unlink(self):
        if self._skip_role_checks():
            return super().unlink()
        for rec in self:
            if not self._is_team_leader(rec.project_id):
                raise AccessError(_('Only Team Leader can delete files.'))
        return super().unlink()
