from odoo import api, fields, models


class NhanVienLink(models.Model):
    _inherit = 'nhan_vien'

    user_id = fields.Many2one('res.users', string='Tai khoan lien ket', index=True)
    project_team_id = fields.Many2one('project.nifty.team', string='Nhom')
    project_lead_team_ids = fields.One2many('project.nifty.team', 'leader_id', string='Nhom dang quan ly')
    project_is_team_lead = fields.Boolean(
        string='La truong nhom',
        compute='_compute_project_is_team_lead',
        store=True,
    )

    @api.depends('project_lead_team_ids')
    def _compute_project_is_team_lead(self):
        for rec in self:
            rec.project_is_team_lead = bool(rec.project_lead_team_ids)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records.filtered(lambda r: not r.user_id and r.email):
            user = self.env['res.users'].search([('email', '=', rec.email)], limit=1)
            if user:
                rec.user_id = user.id
        return records
