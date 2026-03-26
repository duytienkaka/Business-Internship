from odoo import http
from odoo.http import request


class ProjectNiftyAIChatController(http.Controller):
    @http.route('/project_nifty/ai_chat/reply', type='json', auth='user')
    def ai_chat_reply(self, message='', history=None, continue_last=False):
        service = request.env['project.nifty.ai.chat'].sudo()
        result = service.quick_support_reply(message, history or [], bool(continue_last))
        return result or {'reply': 'Khong co phan hoi.'}

    @http.route('/project_nifty/ai_chat/clear', type='json', auth='user')
    def ai_chat_clear(self):
        service = request.env['project.nifty.ai.chat'].sudo()
        return service.quick_support_clear()

    @http.route('/project_nifty/ai_chat/allowed_menu_ids', type='json', auth='user')
    def ai_chat_allowed_menu_ids(self):
        service = request.env['project.nifty.ai.chat'].sudo()
        return service.get_allowed_menu_ids()
