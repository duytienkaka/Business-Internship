from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestProjectNiftyPermissions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        group_user = cls.env.ref('base.group_user')
        Users = cls.env['res.users'].with_context(no_reset_password=True)

        cls.manager = Users.create({
            'name': 'PN Manager',
            'login': 'pn_manager',
            'email': 'pn_manager@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })
        cls.member = Users.create({
            'name': 'PN Member',
            'login': 'pn_member',
            'email': 'pn_member@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })
        cls.outsider = Users.create({
            'name': 'PN Outsider',
            'login': 'pn_outsider',
            'email': 'pn_outsider@example.com',
            'groups_id': [(6, 0, [group_user.id])],
        })
        cls.admin = cls.env.ref('base.user_admin')

        cls.manager_nv = cls.env['nhan_vien'].create({
            'ma_dinh_danh': 'pn_manager',
            'ho_ten_dem': 'PN',
            'ten': 'Manager',
            'email': cls.manager.email,
            'ngay_sinh': '1990-01-01',
            'user_id': cls.manager.id,
        })
        cls.member_nv = cls.env['nhan_vien'].create({
            'ma_dinh_danh': 'pn_member',
            'ho_ten_dem': 'PN',
            'ten': 'Member',
            'email': cls.member.email,
            'ngay_sinh': '1995-01-01',
            'user_id': cls.member.id,
        })
        cls.outsider_nv = cls.env['nhan_vien'].create({
            'ma_dinh_danh': 'pn_outsider',
            'ho_ten_dem': 'PN',
            'ten': 'Outsider',
            'email': cls.outsider.email,
            'ngay_sinh': '1994-01-01',
            'user_id': cls.outsider.id,
        })
        cls.admin_nv = cls.env['nhan_vien'].search([('user_id', '=', cls.admin.id)], limit=1)
        if not cls.admin_nv:
            cls.admin_nv = cls.env['nhan_vien'].create({
                'ma_dinh_danh': 'pn_admin',
                'ho_ten_dem': 'PN',
                'ten': 'Admin',
                'email': cls.admin.email or 'admin@example.com',
                'ngay_sinh': '1988-01-01',
                'user_id': cls.admin.id,
            })

        cls.department = cls.env['don_vi'].search([('ma_don_vi', '=', 'PN-DV')], limit=1)
        if not cls.department:
            cls.department = cls.env['don_vi'].create({
                'ma_don_vi': 'PN-DV',
                'ten_don_vi': 'Phong test permission',
            })

        cls.team = cls.env['project.nifty.team'].create({
            'code': 'PN-TEAM-01',
            'name': 'Permission Team',
            'don_vi_id': cls.department.id,
            'leader_id': cls.manager_nv.id,
        })
        cls.member_nv.project_team_id = cls.team.id
        cls.admin_nv.project_team_id = cls.team.id

        cls.project = cls.env['project.nifty'].with_user(cls.manager).create({
            'name': 'Permission Test Project',
            'description': 'Project for permission checks',
            'team_id': cls.team.id,
            'status': 'in_progress',
        })

        cls.task_list = cls.env['project.nifty.tasklist'].with_user(cls.manager).create({
            'name': 'To Do',
            'project_id': cls.project.id,
            'sequence': 10,
        })

        cls.task = cls.env['project.nifty.task'].with_user(cls.member).create({
            'name': 'Member Task',
            'description': 'Task owned by member',
            'project_id': cls.project.id,
            'tasklist_id': cls.task_list.id,
            'assigned_ids': [(6, 0, [cls.member_nv.id])],
            'status': 'todo',
            'priority': '1',
        })

    def test_member_cannot_change_project_status(self):
        with self.assertRaises(AccessError):
            self.project.with_user(self.member).write({'status': 'completed'})

    def test_member_can_submit_review(self):
        self.task.with_user(self.member).action_submit_for_review()
        self.assertEqual(self.task.status, 'review')

    def test_member_cannot_set_done(self):
        with self.assertRaises(AccessError):
            self.task.with_user(self.member).write({'status': 'done'})

    def test_manager_can_approve_done_and_progress_updates(self):
        self.task.with_user(self.member).write({'status': 'review'})
        self.task.with_user(self.manager).write({'status': 'done'})
        self.project.invalidate_cache(['progress', 'completed_task_count', 'total_task_count'])
        self.assertTrue(self.task.approved_by_manager)
        self.assertEqual(self.project.completed_task_count, 1)
        self.assertEqual(self.project.total_task_count, 1)
        self.assertEqual(self.project.progress, 100.0)

    def test_member_cannot_create_documents_and_files(self):
        with self.assertRaises(AccessError):
            self.env['project.nifty.document'].with_user(self.member).create({
                'name': 'Member doc',
                'project_id': self.project.id,
            })

        with self.assertRaises(AccessError):
            self.env['project.nifty.file'].with_user(self.member).create({
                'name': 'member.txt',
                'project_id': self.project.id,
                'file': 'bWVtYmVyLWZpbGU=',
            })

    def test_manager_can_create_documents_and_files(self):
        doc = self.env['project.nifty.document'].with_user(self.manager).create({
            'name': 'Manager doc',
            'project_id': self.project.id,
            'content': '<p>ok</p>',
        })
        self.assertTrue(doc)

        pfile = self.env['project.nifty.file'].with_user(self.manager).create({
            'name': 'manager.txt',
            'project_id': self.project.id,
            'file': 'bWFuYWdlci1maWxl',
        })
        self.assertEqual(pfile.uploaded_by, self.manager)

    def test_outsider_cannot_access_project(self):
        records = self.env['project.nifty'].with_user(self.outsider).search([('id', '=', self.project.id)])
        self.assertFalse(records)

    def test_admin_can_change_project_manager(self):
        second_manager = self.env['nhan_vien'].create({
            'ma_dinh_danh': 'pn_second_manager',
            'ho_ten_dem': 'PN',
            'ten': 'SecondManager',
            'email': 'pn_second_manager@example.com',
            'ngay_sinh': '1991-01-01',
        })
        second_team = self.env['project.nifty.team'].create({
            'code': 'PN-TEAM-02',
            'name': 'Permission Team 2',
            'don_vi_id': self.department.id,
            'leader_id': second_manager.id,
        })
        self.project.with_user(self.admin).write({'team_id': second_team.id})
        self.assertEqual(self.project.manager_id, second_manager)

    def test_admin_can_create_and_delete_file(self):
        pfile = self.env['project.nifty.file'].with_user(self.admin).create({
            'name': 'admin.txt',
            'project_id': self.project.id,
            'file': 'YWRtaW4tZmlsZQ==',
        })
        self.assertTrue(pfile.exists())
        self.assertEqual(pfile.uploaded_by, self.project.manager_id.user_id)
        pfile.with_user(self.admin).unlink()
        self.assertFalse(pfile.exists())
