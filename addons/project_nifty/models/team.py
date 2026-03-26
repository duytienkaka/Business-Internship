from random import Random

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ProjectNiftyTeam(models.Model):
    _name = 'project.nifty.team'
    _description = 'Nhóm phòng ban cho Project Nifty'
    _order = 'name'

    name = fields.Char('Tên nhóm', required=True)
    code = fields.Char('Mã nhóm', required=True)
    don_vi_id = fields.Many2one('don_vi', string='Phong ban', required=True)
    leader_id = fields.Many2one('nhan_vien', string='Nhóm trưởng', required=True)
    member_ids = fields.One2many('nhan_vien', 'project_team_id', string='Thành viên')
    member_employee_ids = fields.Many2many(
        'nhan_vien',
        string='Thành viên team',
        compute='_compute_member_employee_ids',
        inverse='_inverse_member_employee_ids',
    )

    _sql_constraints = [
        ('project_nifty_team_code_unique', 'unique(code)', 'Mã nhóm phải là duy nhất.'),
        ('project_nifty_team_leader_unique', 'unique(leader_id)', 'Mỗi trưởng nhóm chỉ được quản lý 1 nhóm.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_leader_membership()
        (records.mapped('leader_id') | records.mapped('member_ids'))._sync_project_role_groups()
        return records

    def write(self, vals):
        impacted_employees = self.mapped('leader_id') | self.mapped('member_ids') # type: ignore
        result = super().write(vals)
        self._sync_leader_membership()
        impacted_employees |= self.mapped('leader_id') | self.mapped('member_ids') # type: ignore
        impacted_employees._sync_project_role_groups() # type: ignore
        return result

    def _sync_leader_membership(self):
        for team in self:
            if team.leader_id and team.leader_id.project_team_id != team: # type: ignore
                team.leader_id.project_team_id = team.id # type: ignore

    @api.depends('member_ids')
    def _compute_member_employee_ids(self):
        for team in self:
            team.member_employee_ids = team.member_ids

    def _inverse_member_employee_ids(self):
        for team in self:
            target_members = team.member_employee_ids
            if team.leader_id:
                target_members |= team.leader_id

            to_add = target_members - team.member_ids
            if to_add:
                to_add.write({'project_team_id': team.id})

            to_remove = (team.member_ids - target_members).filtered(lambda emp: emp.project_team_id == team)
            if to_remove:
                to_remove.write({'project_team_id': False})

    def _get_transfer_conflicts(self):
        self.ensure_one()
        conflicts = []
        current_team_id = self.id or self._origin.id
        selected_members = self.member_employee_ids
        if self.leader_id:
            selected_members |= self.leader_id

        for employee in selected_members:
            from_team = employee.project_team_id
            if from_team and from_team.id != current_team_id:
                conflicts.append((employee.ho_va_ten, from_team.name))
        return conflicts

    @api.onchange('member_employee_ids', 'leader_id')
    def _onchange_member_transfer_warning(self):
        for team in self:
            conflicts = team._get_transfer_conflicts()
            if not conflicts:
                continue

            lines = ['- %s (đang ở team: %s)' % (name, from_team) for name, from_team in conflicts]
            return {
                'warning': {
                    'title': _('Xác nhận chuyển thành viên'),
                    'message': _('Các nhân viên sau đang thuộc team khác:\n%s\n\nBấm Lưu để xác nhận chuyển sang team này.') % '\n'.join(lines),
                }
            }

    @api.constrains('leader_id', 'member_ids')
    def _check_single_leader_per_team(self):
        for team in self:
            if not team.leader_id:
                raise ValidationError(_('Each team must have one team leader.'))

            duplicate_team = self.search([
                ('id', '!=', team.id),
                ('leader_id', '=', team.leader_id.id),
            ], limit=1)
            if duplicate_team:
                raise ValidationError(_('Mỗi trưởng nhóm chỉ được quản lý 1 nhóm.'))

    @api.model
    def seed_hr_org_data(self):
        DonVi = self.env['don_vi']
        ChucVu = self.env['chuc_vu']
        NhanVien = self.env['nhan_vien']
        LichSu = self.env['lich_su_cong_tac']

        departments = {
            'DV-CN': 'Phong Cong nghe',
            'DV-KD': 'Phong Kinh doanh',
            'DV-VH': 'Phong Van hanh',
        }
        department_records = {}
        for code, name in departments.items():
            rec = DonVi.search([('ma_don_vi', '=', code)], limit=1)
            if not rec:
                rec = DonVi.create({'ma_don_vi': code, 'ten_don_vi': name})
            department_records[code] = rec

        positions = {
            'CV-TL': 'Trưởng nhóm',
            'CV-CV': 'Chuyen vien',
            'CV-NV': 'Nhân viên',
        }
        position_records = {}
        for code, name in positions.items():
            rec = ChucVu.search([('ma_chuc_vu', '=', code)], limit=1)
            if not rec:
                rec = ChucVu.create({'ma_chuc_vu': code, 'ten_chuc_vu': name})
            position_records[code] = rec

        employee_seed = [
            ('PNNV001', 'Nguyen Van', 'An', '1992-04-15'),
            ('PNNV002', 'Tran Thi', 'Binh', '1993-06-12'),
            ('PNNV003', 'Le Hoang', 'Chi', '1994-03-08'),
            ('PNNV004', 'Pham Minh', 'Dung', '1995-01-20'),
            ('PNNV005', 'Vu Quoc', 'Giang', '1991-10-03'),
            ('PNNV006', 'Do Thi', 'Ha', '1996-07-19'),
            ('PNNV007', 'Bui Anh', 'Khanh', '1997-05-09'),
            ('PNNV008', 'Dang Duc', 'Long', '1992-11-28'),
            ('PNNV009', 'Ngo Thi', 'My', '1995-09-14'),
            ('PNNV010', 'Hoang Gia', 'Nam', '1993-12-02'),
        ]

        rng = Random(20260324)
        team_defs = [
            ('TEAM-CN-A', 'Nhóm Công nghệ A', 'DV-CN'),
            ('TEAM-CN-B', 'Nhóm Công nghệ B', 'DV-CN'),
            ('TEAM-KD-A', 'Nhóm Kinh doanh A', 'DV-KD'),
            ('TEAM-VH-A', 'Nhóm Vận hành A', 'DV-VH'),
        ]

        lead_employee_codes = {'PNNV001', 'PNNV004', 'PNNV007'}
        employees = {}
        for code, ho_ten_dem, ten, birthday in employee_seed:
            email = '%s@example.com' % code.lower()
            employee = NhanVien.search([('ma_dinh_danh', '=', code)], limit=1)
            vals = {
                'ma_dinh_danh': code,
                'ho_ten_dem': ho_ten_dem,
                'ten': ten,
                'email': email,
                'ngay_sinh': birthday,
            }
            if employee:
                employee.write(vals)
            else:
                employee = NhanVien.create(vals)
            employees[code] = employee

        team_records = {}
        available_leads = [employees[code] for code in sorted(lead_employee_codes)]
        for code, name, dep_code in team_defs:
            team = self.search([('code', '=', code)], limit=1)
            if not team:
                leader = available_leads.pop(0) if available_leads else employees['PNNV001']
                team = self.create({
                    'code': code,
                    'name': name,
                    'don_vi_id': department_records[dep_code].id,
                    'leader_id': leader.id,
                })
            else:
                team.write({'name': name, 'don_vi_id': department_records[dep_code].id})
            team_records[code] = team

        non_leads = [employees[code] for code in sorted(employees) if code not in lead_employee_codes]
        assignable_teams = [team_records['TEAM-CN-A'], team_records['TEAM-CN-B'], team_records['TEAM-KD-A'], team_records['TEAM-VH-A']]
        for emp in non_leads:
            team = rng.choice(assignable_teams)
            emp.project_team_id = team.id

        for lead_code, team_code in [('PNNV001', 'TEAM-CN-A'), ('PNNV004', 'TEAM-CN-B'), ('PNNV007', 'TEAM-KD-A')]:
            employees[lead_code].project_team_id = team_records[team_code].id
            team_records[team_code].leader_id = employees[lead_code].id

        for code, employee in employees.items():
            is_lead = code in lead_employee_codes
            title = position_records['CV-TL'] if is_lead else rng.choice([position_records['CV-CV'], position_records['CV-NV']])
            team = employee.project_team_id
            if not team:
                continue
            already = LichSu.search([
                ('nhan_vien_id', '=', employee.id),
                ('don_vi_id', '=', team.don_vi_id.id),
                ('chuc_vu_id', '=', title.id),
                ('loai_chuc_vu', '=', 'Chính'),
            ], limit=1)
            if not already:
                LichSu.create({
                    'nhan_vien_id': employee.id,
                    'don_vi_id': team.don_vi_id.id,
                    'chuc_vu_id': title.id,
                    'loai_chuc_vu': 'Chính',
                })

        return True
