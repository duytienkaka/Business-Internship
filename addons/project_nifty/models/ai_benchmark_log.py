from odoo import fields, models


class ProjectNiftyAIBenchmarkLog(models.Model):
    _name = 'project.nifty.ai.benchmark.log'
    _description = 'Project Nifty AI Benchmark Log'
    _order = 'run_at desc, id desc'

    name = fields.Char('Run Name', required=True)
    run_at = fields.Datetime('Run At', required=True, default=fields.Datetime.now)
    project_id = fields.Many2one('project.nifty', string='Project', required=True, ondelete='cascade')
    status = fields.Selection([
        ('healthy', 'Healthy'),
        ('degraded', 'Degraded'),
        ('no_data', 'No Data'),
    ], string='Status', required=True, default='no_data')
    accuracy = fields.Float('Accuracy (%)', digits=(16, 2))
    min_accuracy = fields.Float('Threshold (%)', digits=(16, 2), default=70.0)
    hit_count = fields.Integer('Hit Count', default=0)
    checked_count = fields.Integer('Checked Count', default=0)
