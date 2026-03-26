import json

from odoo import api, fields, models, _


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_weight_focus_per = fields.Integer(string='AI Focus Weight Per Match', config_parameter='project_nifty.ai_weight_focus_per', default=8)
    ai_weight_focus_cap = fields.Integer(string='AI Focus Weight Cap', config_parameter='project_nifty.ai_weight_focus_cap', default=24)
    ai_weight_keyword_per = fields.Integer(string='AI Keyword Weight Per Match', config_parameter='project_nifty.ai_weight_keyword_per', default=2)
    ai_weight_keyword_cap = fields.Integer(string='AI Keyword Weight Cap', config_parameter='project_nifty.ai_weight_keyword_cap', default=8)
    ai_penalty_profile_mismatch = fields.Integer(string='AI Profile Mismatch Penalty', config_parameter='project_nifty.ai_penalty_profile_mismatch', default=4)
    ai_bonus_same_task = fields.Integer(string='AI Same Task Continuity Bonus', config_parameter='project_nifty.ai_bonus_same_task', default=2)
    ai_bonus_same_team = fields.Integer(string='AI Same Team Bonus', config_parameter='project_nifty.ai_bonus_same_team', default=2)
    ai_bonus_workload_light = fields.Integer(string='AI Workload Light Bonus', config_parameter='project_nifty.ai_bonus_workload_light', default=4)
    ai_bonus_workload_medium = fields.Integer(string='AI Workload Medium Bonus', config_parameter='project_nifty.ai_bonus_workload_medium', default=1)
    ai_penalty_workload_high = fields.Integer(string='AI Workload High Penalty', config_parameter='project_nifty.ai_penalty_workload_high', default=2)
    ai_penalty_overdue_cap = fields.Integer(string='AI Overdue Penalty Cap', config_parameter='project_nifty.ai_penalty_overdue_cap', default=2)
    ai_penalty_critical = fields.Integer(string='AI Critical Load Penalty', config_parameter='project_nifty.ai_penalty_critical', default=1)
    ai_monitor_min_accuracy = fields.Integer(string='AI Monitor Minimum Accuracy (%)', config_parameter='project_nifty.ai_monitor_min_accuracy', default=70)

    ai_benchmark_last_summary = fields.Text(string='AI Benchmark Last Summary', compute='_compute_ai_benchmark_last_summary')

    @api.depends_context('uid')
    def _compute_ai_benchmark_last_summary(self):
        params = self.env['ir.config_parameter'].sudo()
        raw = params.get_param('project_nifty.ai_benchmark_last_summary', '')
        parsed = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {'status': 'invalid', 'raw': raw}

        summary = (
            'Status: %(status)s\n'
            'Accuracy: %(accuracy)s\n'
            'Threshold: %(threshold)s\n'
            'Hit/Checked: %(hit)s/%(checked)s\n'
            'Checked At: %(checked_at)s'
        ) % {
            'status': parsed.get('status', 'n/a'),
            'accuracy': parsed.get('accuracy', 'n/a'),
            'threshold': parsed.get('min_accuracy', 'n/a'),
            'hit': parsed.get('hit', 'n/a'),
            'checked': parsed.get('checked_tasks', 'n/a'),
            'checked_at': parsed.get('checked_at', 'n/a'),
        }
        for rec in self:
            rec.ai_benchmark_last_summary = summary

    def action_run_ai_benchmark_now(self):
        self.ensure_one()
        self.env['project.nifty.task'].sudo().cron_ai_assignment_benchmark_monitor()
        self._compute_ai_benchmark_last_summary()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('AI Benchmark'),
                'message': _('Benchmark monitor has been executed and summary was refreshed.'),
                'sticky': False,
                'type': 'success',
            },
        }
