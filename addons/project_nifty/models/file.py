from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError

class ProjectNiftyFile(models.Model):
    _name = 'project.nifty.file'
    _description = 'Tệp tin dự án Nifty'

    name = fields.Char('File Name', required=True)
    project_id = fields.Many2one('project.nifty', string='Project', required=True, ondelete='cascade')
    available_project_ids = fields.Many2many(
        'project.nifty',
        string='Available Projects',
        compute='_compute_available_project_ids',
    )
    uploaded_by = fields.Many2one('res.users', string='Uploaded By', required=True)
    upload_date = fields.Datetime('Upload Date', default=fields.Datetime.now)
    file = fields.Binary('File', required=True)

    def _skip_role_checks(self):
        return self.env.su or self.env.context.get('install_mode')

    def _is_team_leader(self, project):
        return project._current_is_team_leader()

    def _is_project_creator(self, project):
        return bool(project and project.create_uid == self.env.user)

    def _get_allowed_upload_projects(self):
        employee = self.env['project.nifty']._get_current_nhan_vien()
        domain = [('create_uid', '=', self.env.user.id)]
        if employee:
            domain = ['|', ('manager_id', '=', employee.id), ('create_uid', '=', self.env.user.id)]
        return self.env['project.nifty'].search(domain)

    @api.depends_context('uid')
    def _compute_available_project_ids(self):
        allowed_projects = self._get_allowed_upload_projects()
        for rec in self:
            rec.available_project_ids = allowed_projects

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

        allowed_projects = self._get_allowed_upload_projects()
        for vals in vals_list:
            project = self.env['project.nifty'].browse(vals.get('project_id')).exists()
            if not project:
                raise ValidationError(_('File must belong to a project.'))
            if project not in allowed_projects:
                raise AccessError(_('You can only upload files to projects where you are Team Leader or Project Creator.'))
            if not self._is_team_leader(project) and not self._is_project_creator(project):
                raise AccessError(_('Only Team Leader or Project Creator can upload files.'))
            vals['uploaded_by'] = self.env.user.id
        return super().create(vals_list)

    def write(self, vals):
        if self._skip_role_checks():
            return super().write(vals)

        allowed_projects = self._get_allowed_upload_projects()
        target_project = False
        if vals.get('project_id'):
            target_project = self.env['project.nifty'].browse(vals.get('project_id')).exists()
            if not target_project:
                raise ValidationError(_('File must belong to a project.'))
            if target_project not in allowed_projects:
                raise AccessError(_('You can only upload files to projects where you are Team Leader or Project Creator.'))

        for rec in self:
            project = target_project or rec.project_id
            if not self._is_team_leader(project):
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
