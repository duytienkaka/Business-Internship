from odoo import api, fields, models


class NhanVienLink(models.Model):
    _inherit = 'nhan_vien'

    user_id = fields.Many2one('res.users', string='Tài khoản liên kết', index=True)
    project_team_id = fields.Many2one('project.nifty.team', string='Nhóm')
    project_lead_team_ids = fields.One2many('project.nifty.team', 'leader_id', string='Nhóm đang quản lý')
    project_is_team_lead = fields.Boolean(
        string='Là trưởng nhóm',
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
        records._sync_project_role_groups()
        return records

    def write(self, vals):
        result = super().write(vals)
        self._sync_project_role_groups()
        return result

    def _sync_project_role_groups(self):
        leader_group = self.env.ref('project_nifty.group_project_nifty_team_leader', raise_if_not_found=False)
        member_group = self.env.ref('project_nifty.group_project_nifty_team_member', raise_if_not_found=False)
        manager_group = self.env.ref('project_nifty.group_project_nifty_department_manager', raise_if_not_found=False)
        if not leader_group or not member_group:
            return

        for rec in self:
            user = rec.user_id.sudo()
            if not user:
                continue

            commands = []
            is_department_manager = bool(manager_group and manager_group in user.groups_id)
            if rec.project_is_team_lead:
                commands.append((4, leader_group.id))
                commands.append((3, member_group.id))
            else:
                commands.append((3, leader_group.id))
                if not is_department_manager:
                    commands.append((4, member_group.id))

            if commands:
                user.write({'groups_id': commands})
