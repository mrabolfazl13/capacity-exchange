// Hand-written DTO types per docs/CONTRACTS.md §1/§5 (snake_case, UUID strings,
// ISO-8601 UTC timestamps with Z, money in integer minor units).
// This file is the "generated-style" seam: when the backend exports
// docs/openapi.json, this module can be replaced by codegen output without
// touching feature code, as long as names stay the same.

export type UUID = string;
export type ISODateTime = string;
export type ISODate = string; // YYYY-MM-DD
export type ISOTime = string; // HH:MM:SS
export type CurrencyCode = string; // ISO 4217, char(3)

// ---------- Envelope (§2) ----------

export interface ListEnvelope<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
    request_id?: string;
  };
}

export interface FieldError {
  field: string;
  message: string;
}

// ---------- Enums (§5, lowercase_with_underscore) ----------

export type RoleKey =
  | 'platform_admin'
  | 'support'
  | 'org_admin'
  | 'provider'
  | 'customer';

export type CapacityMode = 'scheduled' | 'quantity' | 'open_ended';
export type ResourceStatus = 'draft' | 'active' | 'archived';
export type OfferPricingMode = 'per_unit_time' | 'per_quantity' | 'flat';
export type BookingMode = 'request_confirm' | 'instant';
export type OfferStatus = 'draft' | 'published' | 'paused' | 'closed';
export type DemandStatus = 'open' | 'matched' | 'closed' | 'cancelled';
export type MatchStatus = 'suggested' | 'accepted' | 'declined' | 'expired';
export type BookingStatus =
  | 'draft'
  | 'hold'
  | 'confirmed'
  | 'in_progress'
  | 'completed'
  | 'cancelled'
  | 'expired'
  | 'disputed';
export type BookingPaymentStatus =
  | 'not_required'
  | 'unpaid'
  | 'paid'
  | 'refunded'
  | 'partially_refunded';
export type OrderStatus =
  | 'draft'
  | 'placed'
  | 'paid'
  | 'fulfilled'
  | 'cancelled'
  | 'refunded';
export type OrderPaymentStatus = BookingPaymentStatus;
export type PaymentStatus =
  | 'created'
  | 'pending'
  | 'succeeded'
  | 'failed'
  | 'canceled'
  | 'refunded';
export type FulfillmentStatus =
  | 'pending'
  | 'in_progress'
  | 'completed'
  | 'no_show'
  | 'failed';
export type OverrideKind = 'closed' | 'extra' | 'reduced';
export type NotificationChannel = 'in_app' | 'email' | 'sms' | 'push';
export type ConversationStatus = 'open' | 'closed' | 'archived';
export type DisputeKind = 'quality' | 'no_show' | 'payment' | 'damage' | 'other';
export type DisputeStatus =
  | 'open'
  | 'under_review'
  | 'resolved_refund'
  | 'resolved_partial'
  | 'resolved_no_fault'
  | 'closed';
export type ReviewStatus = 'published' | 'pending_moderation' | 'removed';
export type PromotionKind = 'coupon' | 'campaign';
export type PromoStatus = 'draft' | 'active' | 'expired' | 'disabled';
export type OrgStaffRole = 'org_admin' | 'manager' | 'staff';

// ---------- Shared value objects ----------

export interface Address {
  line1?: string | null;
  line2?: string | null;
  city?: string | null;
  state?: string | null;
  postal_code?: string | null;
  country?: string | null; // char(2)
}

export interface Photo {
  url: string;
  caption?: string | null;
  sort_order?: number;
}

export interface DocumentRef {
  name: string;
  url: string;
  kind?: string | null;
}

export interface CancellationPolicyBand {
  hours_before: number;
  refund_pct: number;
}

// ---------- Identity & orgs (§5.1) ----------

export interface User {
  id: UUID;
  tenant_id: UUID;
  email: string;
  phone: string | null;
  full_name: string;
  preferred_locale: string;
  is_active: boolean;
  last_login_at: ISODateTime | null;
  created_at: ISODateTime;
  roles: RoleKey[];
  active_org_id: UUID | null;
}

export interface Organization {
  id: UUID;
  tenant_id: UUID;
  name: string;
  slug: string;
  legal_name: string | null;
  country: string | null;
  timezone: string;
  currency: CurrencyCode;
  contact_email: string | null;
  phone: string | null;
  address: Address | null;
  status: 'active' | 'suspended';
  created_at: ISODateTime;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: 'bearer';
  expires_in?: number;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterCustomerRequest {
  email: string;
  password: string;
  full_name: string;
  phone?: string | null;
}

export interface RegisterProviderRequest extends RegisterCustomerRequest {
  account_type: 'provider';
  organization: {
    name: string;
    slug?: string;
    country?: string | null;
    timezone?: string;
    currency?: CurrencyCode;
    contact_email?: string | null;
  };
}

export type RegisterRequest = RegisterCustomerRequest | RegisterProviderRequest;

// ---------- Capacity (§5.2) ----------

export interface CapacityCategory {
  id: UUID;
  key: string;
  label: string;
  parent_id: UUID | null;
  is_active: boolean;
  attributes_schema: Record<string, unknown> | null;
}

export interface CapacityResource {
  id: UUID;
  org_id: UUID;
  category_id: UUID;
  creator_id: UUID;
  name: string;
  description: string | null;
  capacity_mode: CapacityMode;
  address: Address;
  lat: number | null;
  lon: number | null;
  timezone: string;
  status: ResourceStatus;
  attributes: Record<string, unknown>;
  photos: Photo[];
  documents: DocumentRef[];
  created_at: ISODateTime;
  updated_at: ISODateTime;
  /**
   * `CapacityResourceOut` embeds the unit list, and the list route eager-loads it,
   * so every row here carries its definitions.
   */
  definitions?: CapacityDefinition[];
}

export interface CapacityDefinition {
  id: UUID;
  resource_id: UUID;
  name: string;
  unit_label: string;
  min_quantity: number;
  max_quantity: number;
  slot_duration_minutes: number | null;
  buffer_before_minutes: number;
  buffer_after_minutes: number;
  attributes: Record<string, unknown>;
  is_active: boolean;
}

export interface RecurringAvailability {
  id: UUID;
  definition_id: UUID;
  dow: number; // 0=Monday .. 6=Sunday
  start_time: ISOTime;
  end_time: ISOTime;
  quantity: number;
  valid_from: ISODate | null;
  valid_until: ISODate | null;
  is_active: boolean;
}

export interface AvailabilityOverride {
  id: UUID;
  definition_id: UUID;
  override_date: ISODate;
  kind: OverrideKind;
  start_time: ISOTime | null;
  end_time: ISOTime | null;
  quantity: number | null;
  reason: string | null;
}

export interface FreeWindow {
  window_start: ISODateTime;
  window_end: ISODateTime;
  free_quantity: number;
}

export interface AvailabilityDay {
  date: ISODate;
  total_quantity: number;
  booked_quantity: number;
  free_quantity: number;
  closed: boolean;
}

/**
 * GET /capacities/{id}/availability answers for one definition, so it names the
 * definition and the timezone the day plan was cut on.
 */
export interface AvailabilityResponse {
  definition_id: UUID;
  timezone: string;
  days: AvailabilityDay[];
  recurring: RecurringAvailability[];
  overrides: AvailabilityOverride[];
}

// ---------- Offers & demand (§5.3) ----------

export interface Offer {
  id: UUID;
  definition_id: UUID;
  org_id: UUID;
  resource_id: UUID;
  title: string;
  description: string;
  pricing_mode: OfferPricingMode;
  unit_amount_cents: number;
  currency: CurrencyCode;
  min_lead_time_minutes: number;
  max_lead_time_days: number | null;
  min_duration_minutes: number | null;
  max_duration_minutes: number | null;
  min_quantity: number | null;
  max_quantity: number | null;
  booking_mode: BookingMode;
  hold_minutes: number;
  cancellation_policy: CancellationPolicyBand[];
  status: OfferStatus;
  published_at: ISODateTime | null;
  category_id?: UUID | null;
  category_label?: string | null;
  org_name?: string | null;
  city?: string | null;
  rating_avg?: number | null;
  rating_count?: number | null;
  resource_name?: string | null;
  definition_name?: string | null;
  unit_label?: string | null;
  max_quantity_definition?: number | null;
  lat?: number | null;
  lon?: number | null;
  /** Only present when the search carried a window: units still free in it. */
  free_quantity?: number | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface OfferInput {
  definition_id: UUID;
  title: string;
  description: string;
  pricing_mode: OfferPricingMode;
  unit_amount_cents: number;
  currency: CurrencyCode;
  min_lead_time_minutes?: number;
  max_lead_time_days?: number | null;
  min_duration_minutes?: number | null;
  max_duration_minutes?: number | null;
  min_quantity?: number | null;
  max_quantity?: number | null;
  booking_mode: BookingMode;
  hold_minutes?: number;
  cancellation_policy?: CancellationPolicyBand[];
}

export interface OfferSearchParams {
  q?: string;
  category_id?: UUID;
  city?: string;
  country?: string;
  bbox?: string; // min_lon,min_lat,max_lon,max_lat
  from?: ISODateTime;
  to?: ISODateTime;
  min_quantity?: number;
  max_unit_cents?: number;
  min_rating?: number;
  booking_mode?: BookingMode;
  sort?: 'relevance' | 'price_asc' | 'price_desc' | 'newest' | 'rating';
  limit?: number;
  offset?: number;
}

export interface Demand {
  id: UUID;
  customer_id: UUID;
  org_id: UUID | null;
  category_id: UUID | null;
  description: string;
  address: Address | null;
  lat: number | null;
  lon: number | null;
  desired_start: ISODateTime | null;
  desired_end: ISODateTime | null;
  quantity: number;
  budget_min_cents: number | null;
  budget_max_cents: number | null;
  status: DemandStatus;
  expires_at: ISODateTime | null;
  created_at: ISODateTime;
  category_label?: string | null;
  match_count?: number | null;
}

export interface DemandInput {
  category_id?: UUID | null;
  description: string;
  address?: Address | null;
  lat?: number | null;
  lon?: number | null;
  desired_start?: ISODateTime | null;
  desired_end?: ISODateTime | null;
  quantity?: number;
  budget_min_cents?: number | null;
  budget_max_cents?: number | null;
  expires_at?: ISODateTime | null;
}

export interface Match {
  id: UUID;
  demand_id: UUID;
  offer_id: UUID;
  score: number;
  reasons: string[];
  status: MatchStatus;
  provider_action_at: ISODateTime | null;
  offer?: Offer;
  demand?: Demand;
  /** Set when the provider answered with a draft booking. */
  booking_id?: UUID | null;
  created_at: ISODateTime;
}

// ---------- Booking (§5.4) ----------

export interface Booking {
  id: UUID;
  offer_id: UUID;
  definition_id: UUID;
  org_id: UUID;
  customer_id: UUID;
  created_by_user_id: UUID;
  source_match_id: UUID | null;
  status: BookingStatus;
  payment_status: BookingPaymentStatus;
  window_start: ISODateTime;
  window_end: ISODateTime;
  quantity: number;
  unit_amount_cents: number;
  currency: CurrencyCode;
  total_cents: number;
  hold_expires_at: ISODateTime | null;
  request_fingerprint: UUID | null;
  cancel_reason: string | null;
  cancelled_at: ISODateTime | null;
  cancelled_by: UUID | null;
  confirmed_at: ISODateTime | null;
  started_at: ISODateTime | null;
  completed_at: ISODateTime | null;
  dispute_opened_at: ISODateTime | null;
  meta: Record<string, unknown>;
  created_at: ISODateTime;
  offer?: Offer;
  // Optional embeds the detail endpoint may include (tolerated when absent).
  fulfillment?: Fulfillment | null;
  order?: Order | null;
  order_id?: UUID | null;
  review_submitted?: boolean;
  offer_title?: string | null;
  org_name?: string | null;
  resource_name?: string | null;
  customer_name?: string | null;
}

export interface HoldRequest {
  offer_id: UUID;
  window_start: ISODateTime;
  window_end: ISODateTime;
  quantity: number;
  request_fingerprint: UUID;
}

export interface BookingStatusEvent {
  id: UUID;
  booking_id: UUID;
  from_status: BookingStatus | null;
  to_status: BookingStatus;
  actor_user_id: UUID | null;
  reason: string | null;
  meta: Record<string, unknown>;
  created_at: ISODateTime;
}

// ---------- Orders, payments, fulfillment (§5.6) ----------

export interface OrderLineItem {
  description: string;
  qty: number;
  unit_cents: number;
  total_cents: number;
}

export interface Order {
  id: UUID;
  number: string;
  buyer_id: UUID;
  provider_org_id: UUID;
  booking_id: UUID | null;
  promotion_id: UUID | null;
  subtotal_cents: number;
  discount_cents: number;
  commission_cents: number;
  total_cents: number;
  refunded_cents: number;
  currency: CurrencyCode;
  line_items: OrderLineItem[];
  payment_status: OrderPaymentStatus;
  status: OrderStatus;
  placed_at: ISODateTime | null;
  created_at: ISODateTime;
  provider_org_name?: string | null;
}

export interface Payment {
  id: UUID;
  order_id: UUID;
  provider_key: string;
  amount_cents: number;
  currency: CurrencyCode;
  status: PaymentStatus;
  client_secret: string | null;
  provider_payment_id: string | null;
  failure_reason: string | null;
  confirmed_at: ISODateTime | null;
  created_at: ISODateTime;
}

export interface FulfillmentNote {
  body: string;
  author_id?: UUID | null;
  created_at?: ISODateTime | null;
}

export interface Fulfillment {
  id: UUID;
  booking_id: UUID;
  org_id: UUID;
  status: FulfillmentStatus;
  started_at: ISODateTime;
  completed_at: ISODateTime | null;
  notes: FulfillmentNote[];
  completed_by: UUID | null;
}

// ---------- Reviews ----------

export interface Review {
  id: UUID;
  booking_id: UUID;
  fulfillment_id: UUID;
  offer_id: UUID;
  org_id: UUID;
  reviewer_id: UUID;
  rating: number; // 1..5
  comment: string | null;
  provider_reply: string | null;
  replied_at: ISODateTime | null;
  status: ReviewStatus;
  created_at: ISODateTime;
  reviewer_name?: string | null;
  offer_title?: string | null;
}

export interface ReviewInput {
  booking_id: UUID;
  fulfillment_id: UUID;
  rating: number;
  comment?: string | null;
}

// ---------- Notifications, conversations, disputes ----------

export interface AppNotification {
  id: UUID;
  user_id: UUID;
  org_id: UUID | null;
  kind: string;
  title: string;
  body: string;
  data: Record<string, unknown>;
  channel: NotificationChannel;
  read_at: ISODateTime | null;
  sent_at: ISODateTime | null;
  created_at: ISODateTime;
}

export interface Conversation {
  id: UUID;
  kind: string;
  ref_id: UUID | null;
  org_id: UUID | null;
  customer_id: UUID;
  provider_org_id: UUID;
  status: ConversationStatus;
  last_message_at: ISODateTime;
  created_at: ISODateTime;
  /** The other side of the thread, named by whoever wrote the last message. */
  peer_name?: string | null;
  last_message_body?: string | null;
  unread_count?: number;
}

export interface Message {
  id: UUID;
  conversation_id: UUID;
  sender_id: UUID;
  body: string;
  is_system: boolean;
  read_receipts: Record<string, ISODateTime>;
  created_at: ISODateTime;
  sender_name?: string | null;
}

export interface Dispute {
  id: UUID;
  booking_id: UUID;
  org_id: UUID;
  complainant_id: UUID;
  kind: DisputeKind;
  description: string;
  status: DisputeStatus;
  resolution_note: string | null;
  resolved_by: UUID | null;
  resolved_at: ISODateTime | null;
  created_at: ISODateTime;
  /** Money the resolution moved back; 0 for the no-fault and open paths. */
  refund_cents?: number;
  complainant_name?: string | null;
  org_name?: string | null;
}

// ---------- Admin ----------

export interface AuditLog {
  id: UUID;
  actor_user_id: UUID | null;
  actor_org_id: UUID | null;
  action: string;
  entity_type: string;
  entity_id: UUID | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  ip: string | null;
  user_agent: string | null;
  request_id: UUID | null;
  created_at: ISODateTime;
}

export interface AdminProvider {
  id: UUID;
  name: string;
  slug: string;
  status: 'active' | 'suspended';
  org_admin_email?: string | null;
  offer_count?: number;
  booking_count?: number;
  country?: string | null;
  currency?: CurrencyCode;
  timezone?: string;
  gmv_cents?: number;
  rating_avg?: number | null;
  rating_count?: number;
  open_disputes?: number;
  created_at: ISODateTime;
}

export interface Promotion {
  id: UUID;
  name: string;
  kind: PromotionKind;
  code: string | null;
  discount_config: Record<string, unknown>;
  min_order_cents: number;
  applies_to: Record<string, unknown>;
  usage_limit: number | null;
  used_count: number;
  per_user_limit: number | null;
  starts_at: ISODateTime;
  ends_at: ISODateTime;
  status: PromoStatus;
  created_by: UUID | null;
  created_at: ISODateTime;
  created_by_name?: string | null;
}

// ---------- Dashboards (§8) ----------

export interface CustomerDashboard {
  active_bookings: Booking[];
  spend_cents: number;
  spend_currency: CurrencyCode;
  unread_notifications: number;
  recent_orders: Order[];
}

export interface TopOfferRow {
  offer_id: UUID;
  title: string;
  bookings: number;
  revenue_cents: number;
}

export interface ProviderDashboard {
  utilization_pct: number;
  bookings_by_status: Partial<Record<BookingStatus, number>>;
  revenue_cents: number;
  revenue_currency: CurrencyCode;
  upcoming_bookings: Booking[];
  top_offers: TopOfferRow[];
}

export interface AdminDashboard {
  users_total: number;
  orgs_total: number;
  offers_total: number;
  bookings_total: number;
  gmv_cents: number;
  currency: CurrencyCode;
  commission_cents: number;
  open_disputes: number;
}

export interface TopCategoryRow {
  category_id: UUID;
  label: string;
  bookings: number;
  revenue_cents: number;
}

export interface AnalyticsDay {
  date: ISODate;
  bookings_created: number;
  orders_placed: number;
  gmv_cents: number;
}

/**
 * GET /admin/analytics — every key names its own clock: volume is event-dated,
 * money is settlement-dated.
 */
export interface PlatformAnalytics {
  from: ISODateTime;
  to: ISODateTime;
  users_created: number;
  providers_created: number;
  offers_created: number;
  bookings_created: number;
  bookings_completed: number;
  bookings_cancelled: number;
  orders_placed: number;
  gmv_cents: number;
  commission_cents: number;
  refunded_cents: number;
  reviews_published: number;
  rating_avg: number | null;
  disputes_opened: number;
  disputes_resolved: number;
  top_categories: TopCategoryRow[];
  daily: AnalyticsDay[];
}
