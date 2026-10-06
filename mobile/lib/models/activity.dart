import 'json.dart';

/// `notifications` (CONTRACTS §5.7). Clients poll `unread=true` when SSE is
/// not consumed (§1).
class NotificationItem {
  const NotificationItem({
    required this.id,
    required this.kind,
    required this.title,
    required this.body,
    required this.channel,
    required this.isRead,
    this.data = const {},
    this.createdAt,
    this.readAt,
  });

  final String id;

  /// free-form server kind, e.g. `booking.confirmed`, `hold.expired`
  final String kind;
  final String title;
  final String body;

  /// in_app | email | sms | push
  final String channel;
  final bool isRead;
  final Map<String, dynamic> data;
  final DateTime? createdAt;
  final DateTime? readAt;

  /// Deep-link hints carried in `data` per the schema.
  String? get bookingId => Json.str(data, 'booking_id');
  String? get offerId => Json.str(data, 'offer_id');
  String? get orderId => Json.str(data, 'order_id');
  String? get conversationId => Json.str(data, 'conversation_id');
  String? get demandId => Json.str(data, 'demand_id');

  static NotificationItem fromJson(Map<String, dynamic> json) => NotificationItem(
        id: Json.strOr(json, 'id'),
        kind: Json.strOr(json, 'kind'),
        title: Json.strOr(json, 'title'),
        body: Json.strOr(json, 'body'),
        channel: Json.strOr(json, 'channel', 'in_app'),
        isRead: Json.date(json, 'read_at') != null || Json.boolOr(json, 'is_read'),
        data: Json.map(json, 'data'),
        createdAt: Json.date(json, 'created_at'),
        readAt: Json.date(json, 'read_at'),
      );
}

/// `conversations` + `messages` (CONTRACTS §5.7).
class Conversation {
  const Conversation({
    required this.id,
    required this.kind,
    required this.status,
    required this.customerId,
    required this.providerOrgId,
    this.refId,
    this.subject = '',
    this.lastMessageAt,
    this.unreadCount = 0,
  });

  final String id;

  /// typically `booking`
  final String kind;
  final String? refId;

  /// open | closed | archived
  final String status;
  final String customerId;
  final String providerOrgId;
  final String subject;
  final DateTime? lastMessageAt;
  final int unreadCount;

  static Conversation fromJson(Map<String, dynamic> json) => Conversation(
        id: Json.strOr(json, 'id'),
        kind: Json.strOr(json, 'kind', 'booking'),
        refId: Json.str(json, 'ref_id'),
        status: Json.strOr(json, 'status', 'open'),
        customerId: Json.strOr(json, 'customer_id'),
        providerOrgId: Json.strOr(json, 'provider_org_id'),
        subject: Json.strOr(json, 'subject', Json.strOr(json, 'title')),
        lastMessageAt: Json.date(json, 'last_message_at'),
        unreadCount: Json.intOr(json, 'unread_count'),
      );
}

class Message {
  const Message({
    required this.id,
    required this.conversationId,
    required this.senderId,
    required this.body,
    required this.isSystem,
    this.createdAt,
  });

  final String id;
  final String conversationId;
  final String senderId;
  final String body;
  final bool isSystem;
  final DateTime? createdAt;

  static Message fromJson(Map<String, dynamic> json) => Message(
        id: Json.strOr(json, 'id'),
        conversationId: Json.strOr(json, 'conversation_id'),
        senderId: Json.strOr(json, 'sender_id'),
        body: Json.strOr(json, 'body'),
        isSystem: Json.boolOr(json, 'is_system'),
        createdAt: Json.date(json, 'created_at'),
      );
}
