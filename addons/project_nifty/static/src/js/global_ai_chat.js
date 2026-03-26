odoo.define('project_nifty.global_ai_chat', function (require) {
    'use strict';

    const Widget = require('web.Widget');
    const rpc = require('web.rpc');
    const ajax = require('web.ajax');
    const domReady = require('web.dom_ready');

    const STORAGE_OPEN_KEY = 'project_nifty_ai_chat_open';
    const STORAGE_HISTORY_KEY = 'project_nifty_ai_chat_history';

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function extractErrorMessage(error) {
        if (!error) {
            return 'Đã xảy ra lỗi khi gọi AI.';
        }

        const data = error && error.data ? error.data : null;
        if (data && Array.isArray(data.arguments) && data.arguments.length) {
            const arg0 = data.arguments[0];
            if (typeof arg0 === 'string' && arg0.trim()) {
                return arg0;
            }
        }

        const candidates = [
            data && data.message,
            data && data.exception_type,
            data && data.debug,
            error && error.message,
            error && error.statusText,
        ];

        for (let i = 0; i < candidates.length; i += 1) {
            const item = candidates[i];
            if (!item) {
                continue;
            }
            if (typeof item === 'string') {
                if (item.trim() === 'Odoo Server Error') {
                    continue;
                }
                return item;
            }
            if (typeof item === 'object') {
                if (item.message && typeof item.message === 'string') {
                    return item.message;
                }
                try {
                    return JSON.stringify(item);
                } catch (e) {
                    // ignore stringify error
                }
            }
        }
        return 'Đã xảy ra lỗi khi gọi AI.';
    }

    function applyInlineMarkdown(escapedText) {
        let result = escapedText;
        result = result.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        result = result.replace(/\*(.+?)\*/g, '<em>$1</em>');
        return result;
    }

    function renderMessageContent(content) {
        const escaped = escapeHtml(content || '');
        const lines = escaped.split(/\r?\n/);
        let html = '';
        let inList = false;

        for (let i = 0; i < lines.length; i += 1) {
            const line = lines[i];
            const trimmed = line.trim();
            const bulletMatch = trimmed.match(/^([*-])\s+(.+)$/);

            if (bulletMatch) {
                if (!inList) {
                    html += '<ul class="project_nifty_ai_chat_list">';
                    inList = true;
                }
                html += '<li>' + applyInlineMarkdown(bulletMatch[2]) + '</li>';
                continue;
            }

            if (inList) {
                html += '</ul>';
                inList = false;
            }

            if (!trimmed) {
                html += '<br/>';
                continue;
            }

            html += '<div class="project_nifty_ai_chat_line">' + applyInlineMarkdown(line) + '</div>';
        }

        if (inList) {
            html += '</ul>';
        }

        return html;
    }

    const GlobalAIChatWidget = Widget.extend({
        renderElement: function () {
            this.setElement($(
                '<div class="project_nifty_ai_chat_root" role="complementary" aria-label="AI Chat Assistant">'
                + '<button class="project_nifty_ai_chat_toggle" type="button" aria-expanded="false" title="Mo dong tro ly AI">'
                + '<i class="fa fa-comments" title="AI Chat"></i>'
                + '</button>'
                + '<div class="project_nifty_ai_chat_panel" style="display: none;">'
                + '<div class="project_nifty_ai_chat_header">'
                + '<div class="project_nifty_ai_chat_title">Project Nifty AI</div>'
                + '<button type="button" class="project_nifty_ai_chat_clear" title="Xoa doan chat">Clear</button>'
                + '</div>'
                + '<div class="project_nifty_ai_chat_status">San sang</div>'
                + '<div class="project_nifty_ai_chat_body"></div>'
                + '<div class="project_nifty_ai_chat_footer">'
                + '<textarea class="project_nifty_ai_chat_input" rows="2" placeholder="Nhập nội dung và nhấn Enter..."></textarea>'
                + '<button type="button" class="project_nifty_ai_chat_send">Send</button>'
                + '</div>'
                + '</div>'
                + '</div>'
            ));
        },

        start: function () {
            this.history = this._loadHistory();
            this.isOpen = this._loadOpenState();
            this._lastMenuMatch = false;
            this._projectNiftyMenuIds = new Set();

            this.$el.on('click', '.project_nifty_ai_chat_toggle', this._onToggleChat.bind(this));
            this.$el.on('click', '.project_nifty_ai_chat_send', this._onSendMessage.bind(this));
            this.$el.on('click', '.project_nifty_ai_chat_clear', this._onClearMessages.bind(this));
            this.$el.on('keydown', '.project_nifty_ai_chat_input', this._onInputKeyDown.bind(this));

            this.$body = this.$('.project_nifty_ai_chat_body');
            this.$panel = this.$('.project_nifty_ai_chat_panel');
            this.$input = this.$('.project_nifty_ai_chat_input');
            this._renderMessages();
            this._updateOpenState();

            this._onHashChange = this._refreshVisibility.bind(this);
            window.addEventListener('hashchange', this._onHashChange);

            this._refreshVisibility();

            return Promise.resolve(this._super.apply(this, arguments)).then(() => {
                return this._loadProjectNiftyMenuIds().then(() => {
                    this._refreshVisibility();
                }).catch(() => {
                    this._refreshVisibility();
                });
            });
        },

        destroy: function () {
            if (this._onHashChange) {
                window.removeEventListener('hashchange', this._onHashChange);
            }
            return this._super.apply(this, arguments);
        },

        _onToggleChat: function () {
            this.isOpen = !this.isOpen;
            this._saveOpenState();
            this._updateOpenState();
            if (this.isOpen) {
                this.$input.trigger('focus');
            }
        },

        _onInputKeyDown: function (ev) {
            if (ev.key === 'Enter' && !ev.shiftKey) {
                ev.preventDefault();
                this._onSendMessage();
            }
        },

        _onSendMessage: function () {
            const message = (this.$input.val() || '').trim();
            if (!message) {
                return;
            }

            const contextPayload = this._buildChatContextPayload();

            this._pushMessage('user', message);
            this.$input.val('');
            this._renderMessages();

            this._setPendingState(true);
            ajax.jsonRpc('/project_nifty/ai_chat/reply', 'call', {
                message: message,
                history: this.history,
                context_payload: contextPayload,
            }).then((result) => {
                const reply = (result && result.reply) || 'Không có phản hồi.';
                if (reply) {
                    this._pushMessage('assistant', reply);
                    this._renderMessages();
                }
                if (result && result.pending_continuation) {
                    return this._continuePendingReply();
                }
                return Promise.resolve();
            }).guardedCatch((error) => {
                const errorMessage = extractErrorMessage(error);
                if (window.console && window.console.error) {
                    window.console.error('[project_nifty_ai_chat] RPC error:', error);
                }
                this._pushMessage('assistant', 'Loi: ' + errorMessage);
                this._renderMessages();
            }).finally(() => {
                this._setPendingState(false);
                this.$input.trigger('focus');
            });
        },

        _continuePendingReply: function () {
            const maxChunks = 8;
            let loops = 0;
            let reachedLoopCap = false;

            const fetchNext = () => {
                if (loops >= maxChunks) {
                    reachedLoopCap = true;
                    return Promise.resolve();
                }
                loops += 1;
                this.$('.project_nifty_ai_chat_status').text('Đang tiếp tục câu trả lời...');

                return ajax.jsonRpc('/project_nifty/ai_chat/reply', 'call', {
                    message: '',
                    history: this.history,
                    continue_last: true,
                }).then((result) => {
                    const chunk = ((result && result.reply) || '').trim();
                    if (chunk) {
                        this._appendAssistantChunk(chunk);
                        this._renderMessages();
                    }
                    if (result && result.pending_continuation) {
                        return fetchNext();
                    }
                    return Promise.resolve();
                }).guardedCatch((error) => {
                    const errorMessage = extractErrorMessage(error);
                    this._pushMessage('assistant', 'Lỗi khi tiếp tục: ' + errorMessage);
                    this._renderMessages();
                    return Promise.resolve();
                });
            };

            return fetchNext().then(() => {
                if (reachedLoopCap) {
                    this._pushMessage('assistant', 'Nội dung hơi dài nên đã tạm dừng tiếp tục. Bạn bấm Send thêm một lần nữa để lấy phần còn lại.');
                    this._renderMessages();
                }
            });
        },

        _onClearMessages: function () {
            this.history = [];
            this._saveHistory();
            this._renderMessages();
            ajax.jsonRpc('/project_nifty/ai_chat/clear', 'call', {});
        },

        _pushMessage: function (role, content) {
            this.history.push({ role: role, content: content });
            this.history = this.history.slice(-30);
            this._saveHistory();
        },

        _appendAssistantChunk: function (chunk) {
            if (!chunk) {
                return;
            }
            const lastIndex = this.history.length - 1;
            if (lastIndex >= 0 && this.history[lastIndex].role === 'assistant') {
                this.history[lastIndex].content = (this.history[lastIndex].content || '') + '\n' + chunk;
                this._saveHistory();
                return;
            }
            this._pushMessage('assistant', chunk);
        },

        _renderMessages: function () {
            let html = '<div class="project_nifty_ai_chat_messages">';
            if (!this.history.length) {
                html += '<div class="project_nifty_ai_chat_empty">Chao ban! Minh la tro ly AI, ban can ho tro gi?</div>';
            } else {
                this.history.forEach((msg) => {
                    const isUser = msg.role === 'user';
                    html += '<div class="project_nifty_ai_chat_message ' + (isUser ? 'is-user' : 'is-assistant') + '">';
                    html += '<div class="project_nifty_ai_chat_message_role">' + (isUser ? 'Ban' : 'AI') + '</div>';
                    html += '<div class="project_nifty_ai_chat_message_text">' + renderMessageContent(msg.content) + '</div>';
                    html += '</div>';
                });
            }
            html += '</div>';
            this.$body.html(html);
            this.$body.scrollTop(this.$body[0].scrollHeight);
        },

        _setPendingState: function (isPending) {
            this.$('.project_nifty_ai_chat_send').prop('disabled', isPending);
            this.$('.project_nifty_ai_chat_input').prop('disabled', isPending);
            this.$('.project_nifty_ai_chat_status').text(isPending ? 'Đang tạo phản hồi...' : 'Sẵn sàng');
        },

        _loadOpenState: function () {
            return localStorage.getItem(STORAGE_OPEN_KEY) === '1';
        },

        _saveOpenState: function () {
            localStorage.setItem(STORAGE_OPEN_KEY, this.isOpen ? '1' : '0');
        },

        _loadHistory: function () {
            try {
                const raw = localStorage.getItem(STORAGE_HISTORY_KEY);
                const parsed = raw ? JSON.parse(raw) : [];
                return Array.isArray(parsed) ? parsed : [];
            } catch (e) {
                return [];
            }
        },

        _saveHistory: function () {
            localStorage.setItem(STORAGE_HISTORY_KEY, JSON.stringify(this.history));
        },

        _updateOpenState: function () {
            this.$el.toggleClass('is-open', this.isOpen);
            this.$panel.toggle(this.isOpen);
            this.$('.project_nifty_ai_chat_toggle').attr('aria-expanded', this.isOpen ? 'true' : 'false');
        },

        _loadProjectNiftyMenuIds: function () {
            return ajax.jsonRpc('/project_nifty/ai_chat/allowed_menu_ids', 'call', {}).then((menuIds) => {
                this._projectNiftyMenuIds = new Set(menuIds || []);
            });
        },

        _refreshVisibility: function () {
            const isAllowed = this._isProjectNiftyContext();
            this._setVisibility(isAllowed);
        },

        _isProjectNiftyContext: function () {
            const hash = (window.location.hash || '').replace(/^#/, '');
            if (!hash) {
                return false;
            }

            const params = new URLSearchParams(hash);
            const model = String(params.get('model') || '').toLowerCase();
            if (model.indexOf('project.nifty') === 0) {
                return true;
            }

            const action = String(params.get('action') || '').toLowerCase();
            if (action.indexOf('project_nifty.') === 0) {
                return true;
            }

            const menuId = this._currentMenuId();
            if (!menuId) {
                return false;
            }

            if (!this._projectNiftyMenuIds.size) {
                return this._lastMenuMatch;
            }

            const matched = this._projectNiftyMenuIds.has(menuId);
            this._lastMenuMatch = matched;
            return matched;
        },

        _currentMenuId: function () {
            const hash = (window.location.hash || '').replace(/^#/, '');
            if (!hash) {
                return null;
            }
            const params = new URLSearchParams(hash);
            const raw = params.get('menu_id');
            if (!raw) {
                return null;
            }
            const id = parseInt(raw, 10);
            return Number.isFinite(id) ? id : null;
        },

        _buildChatContextPayload: function () {
            const hash = (window.location.hash || '').replace(/^#/, '');
            if (!hash) {
                return {};
            }
            const params = new URLSearchParams(hash);
            const model = String(params.get('model') || '').trim();
            const action = String(params.get('action') || '').trim();
            const menuId = this._currentMenuId();
            const resIdRaw = String(params.get('id') || '').trim();
            const resId = parseInt(resIdRaw, 10);

            return {
                model: model,
                action: action,
                menu_id: Number.isFinite(menuId) ? menuId : false,
                res_id: Number.isFinite(resId) ? resId : false,
            };
        },

        _setVisibility: function (isVisible) {
            this.$el.toggle(!!isVisible);
            if (!isVisible) {
                this.$panel.hide();
            } else {
                this._updateOpenState();
            }
        },
    });

    function mountChatWidget() {
        const rootEl = document.querySelector('.o_web_client');
        if (!rootEl) {
            return false;
        }
        if (rootEl.querySelector('.project_nifty_ai_chat_root')) {
            return true;
        }
        const widget = new GlobalAIChatWidget(null);
        widget.appendTo(rootEl);
        return true;
    }

    function bootWidget() {
        if (mountChatWidget()) {
            return;
        }

        let tries = 0;
        const timer = setInterval(function () {
            tries += 1;
            if (mountChatWidget() || tries > 40) {
                clearInterval(timer);
            }
        }, 300);
    }

    if (domReady && typeof domReady.then === 'function') {
        domReady.then(bootWidget);
    } else if (typeof domReady === 'function') {
        domReady(bootWidget);
    } else if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootWidget);
    } else {
        bootWidget();
    }
});
