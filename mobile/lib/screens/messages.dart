import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/formatters.dart';
import '../core/l10n.dart';
import '../data/repo.dart';
import '../models/activity.dart';
import '../state/session.dart';
import '../widgets/common.dart';

/// Threads the caller is party to — a booking thread with the provider, or the
/// provider's side of the same conversation. Which one depends on the role, so
/// the query flag comes from the session, not from a toggle.
class MessagesScreen extends StatelessWidget {
  const MessagesScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final repo = context.read<Repo>();
    final provider = context.watch<Session>().isProvider;
    return AsyncView<List<Conversation>>(
      loader: () async => (await repo.conversations(provider: provider)).items,
      builder: (context, items) => ListView(
        padding: const EdgeInsets.only(bottom: 96),
        children: [
          for (final c in items)
            ListTile(
              leading: CircleAvatar(
                child: Text(
                  c.peerName.isEmpty ? '?' : c.peerName.substring(0, 1),
                ),
              ),
              title: Text(
                c.peerName.isEmpty ? l.t('booking_thread') : c.peerName,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              subtitle: Text(
                [
                  if (c.lastMessageBody.isNotEmpty) c.lastMessageBody,
                  if (c.lastMessageAt != null)
                    Format.relative(c.lastMessageAt!),
                ].join(' · '),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodySmall,
              ),
              trailing: c.unreadCount > 0
                  ? Badge.count(count: c.unreadCount)
                  : null,
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => ConversationScreen(
                    conversationId: c.id,
                    title: c.peerName.isEmpty
                        ? l.t('booking_thread')
                        : c.peerName,
                  ),
                ),
              ),
            ),
        ],
      ),
      onEmpty: (context, items) => items.isEmpty
          ? EmptyView(message: l.t('no_messages'), icon: Icons.forum_outlined)
          : null,
    );
  }
}

/// One thread. Sending appends the server's row rather than a locally
/// fabricated one, so a rejected message never appears as delivered.
class ConversationScreen extends StatefulWidget {
  const ConversationScreen({
    super.key,
    required this.conversationId,
    this.title,
  });

  final String conversationId;
  final String? title;

  @override
  State<ConversationScreen> createState() => _ConversationScreenState();
}

class _ConversationScreenState extends State<ConversationScreen> {
  final _input = TextEditingController();
  late Future<List<Message>> _future;
  bool _sending = false;

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  Future<List<Message>> _load() => context
      .read<Repo>()
      .messages(widget.conversationId)
      .then((page) => page.items);

  void _refresh() => setState(() => _future = _load());

  Future<void> _send() async {
    final body = _input.text.trim();
    if (body.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      await context.read<Repo>().sendMessage(widget.conversationId, body);
      _input.clear();
      _refresh();
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final me = context.watch<Session>().user?.id;
    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.title == null || widget.title!.isEmpty
              ? l.t('booking_thread')
              : widget.title!,
        ),
        actions: [
          IconButton(
            onPressed: _refresh,
            icon: const Icon(Icons.refresh_outlined),
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: AsyncView<List<Message>>(
                key: ValueKey(_future),
                loader: () => _future,
                builder: (context, messages) => ListView.builder(
                  reverse: true,
                  padding: const EdgeInsets.all(12),
                  itemCount: messages.length,
                  itemBuilder: (context, i) {
                    final m = messages[messages.length - 1 - i];
                    return _bubble(m, m.senderId == me);
                  },
                ),
                onEmpty: (context, messages) => messages.isEmpty
                    ? EmptyView(
                        message: l.t('no_messages'),
                        icon: Icons.chat_bubble_outline,
                      )
                    : null,
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _input,
                      minLines: 1,
                      maxLines: 4,
                      textInputAction: TextInputAction.send,
                      onSubmitted: (_) => _send(),
                      decoration: InputDecoration(
                        hintText: l.t('message_hint'),
                        border: const OutlineInputBorder(),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  FilledButton(
                    onPressed: _sending ? null : _send,
                    child: Text(l.t('send')),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _bubble(Message m, bool mine) {
    final theme = Theme.of(context);
    final bg = m.isSystem
        ? theme.colorScheme.surfaceContainerHighest
        : mine
        ? theme.colorScheme.primaryContainer
        : theme.colorScheme.secondaryContainer;
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        constraints: const BoxConstraints(maxWidth: 420),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Column(
          crossAxisAlignment: mine
              ? CrossAxisAlignment.end
              : CrossAxisAlignment.start,
          children: [
            Text(m.body),
            if (m.createdAt != null)
              Text(
                Format.time(m.createdAt!),
                style: theme.textTheme.labelSmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
