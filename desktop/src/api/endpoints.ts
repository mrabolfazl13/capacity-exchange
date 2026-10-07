// Typed endpoint functions for the full CONTRACTS §8 surface. Every feature
// module goes through this file — no ad-hoc fetch calls in components — so the
// client boundary is a single mockable seam and paths/queries stay consistent.

import { api, uuidv4 } from '@/api/client';
import type {
  AdminDisputeRow,
  AdminProvider,
  AdminDashboard,
  AppNotification,
  AuditLog,
  AvailabilityOverride,
  AvailabilityResponse,
  Booking,
  BookingStatusEvent,
  CapacityCategory,
  CapacityDefinition,
  CapacityResource,
  Conversation,
  CopilotBriefing,
  CurrencyCode,
  CustomerDashboard,
  Demand,
  DemandInput,
  Dispute,
  DisputeStatus,
  FreeWindow,
  Fulfillment,
  HoldRequest,
  ListEnvelope,
  ListingDraft,
  Match,
  Message,
  Offer,
  OfferInput,
  OfferSearchParams,
  OfferStatus,
  Order,
  Organization,
  OrgStaffRole,
  ParsedQuery,
  Payment,
  PlatformAnalytics,
  PriceSuggestion,
  Promotion,
  PromotionRedemption,
  ProviderDashboard,
  RecurringAvailability,
  Review,
  ReviewInput,
  RoleKey,
  TokenPair,
  UtilizationInsights,
  UserRole,
  User,
  UUID,
} from '@/types/api';

// ---------- helpers ----------

function toIso(d: Date | string): string {
  return d instanceof Date ? d.toISOString() : d;
}

/** Serialize offer search filters to the GET /offers querystring (CONTRACTS §8). */
export function offerSearchQuery(
  params: OfferSearchParams,
): Record<string, string | number | boolean | undefined | null> {
  return {
    q: params.q || undefined,
    category_id: params.category_id || undefined,
    city: params.city || undefined,
    country: params.country || undefined,
    bbox: params.bbox || undefined,
    from: params.from ? toIso(params.from) : undefined,
    to: params.to ? toIso(params.to) : undefined,
    min_quantity: params.min_quantity ?? undefined,
    max_unit_cents: params.max_unit_cents ?? undefined,
    min_rating: params.min_rating ?? undefined,
    booking_mode: params.booking_mode || undefined,
    sort: params.sort || undefined,
    limit: params.limit ?? 20,
    offset: params.offset ?? 0,
  };
}

// ---------- auth (§3) ----------

export const authApi = {
  login(email: string, password: string) {
    return api.post<TokenPair>('/auth/login', { email, password });
  },
  register(payload: Record<string, unknown>) {
    return api.post<TokenPair & { user?: User }>('/auth/register', payload);
  },
  logout(refreshToken: string | null) {
    return api.post<{ ok: boolean }>('/auth/logout', {
      refresh_token: refreshToken,
    });
  },
  me() {
    return api.get<User>('/auth/me');
  },
  patchMe(patch: { full_name?: string; preferred_locale?: string; phone?: string }) {
    return api.patch<User>('/auth/me', patch);
  },
};

// ---------- organizations ----------

export const orgApi = {
  create(payload: {
    name: string;
    slug?: string;
    country?: string;
    timezone?: string;
    currency?: string;
    contact_email?: string;
  }) {
    return api.post<Organization>('/organizations', payload);
  },
  mine() {
    return api.get<ListEnvelope<Organization>>('/organizations/mine');
  },
  get(id: UUID) {
    return api.get<Organization>(`/organizations/${id}`);
  },
  patch(id: UUID, patch: Partial<Organization>) {
    return api.patch<Organization>(`/organizations/${id}`, patch);
  },
  staff(orgId: UUID) {
    return api.get<ListEnvelope<OrgStaffRow>>(`/organizations/${orgId}/staff`);
  },
  inviteStaff(orgId: UUID, email: string, role: OrgStaffRole) {
    return api.post<OrgStaffRow>(`/organizations/${orgId}/staff`, { email, role });
  },
  revokeStaff(orgId: UUID, userId: UUID) {
    return api.del<void>(`/organizations/${orgId}/staff/${userId}`);
  },
};

// ---------- catalog ----------

export const catalogApi = {
  categories() {
    return api.get<ListEnvelope<CapacityCategory>>('/catalog/categories');
  },
};

// ---------- capacity (provider side, §5.2 / §8) ----------

export interface CapacityResourceInput {
  category_id: UUID;
  org_id?: UUID;
  name: string;
  description?: string | null;
  capacity_mode: 'scheduled' | 'quantity' | 'open_ended';
  address: {
    line1?: string | null;
    line2?: string | null;
    city?: string | null;
    state?: string | null;
    postal_code?: string | null;
    country?: string | null;
  };
  lat?: number | null;
  lon?: number | null;
  timezone?: string;
  attributes?: Record<string, unknown>;
  photos?: { url: string; caption?: string | null; sort_order?: number }[];
  documents?: { name: string; url: string; kind?: string | null }[];
  /** The resource route takes its units inline, so the wizard publishes in one call. */
  definitions?: DefinitionInput[];
}

export interface DefinitionInput {
  name: string;
  unit_label: string;
  min_quantity: number;
  max_quantity: number;
  slot_duration_minutes?: number | null;
  buffer_before_minutes?: number;
  buffer_after_minutes?: number;
  attributes?: Record<string, unknown>;
}

export interface RecurringAvailabilityInput {
  definition_id?: UUID | null;
  dow: number;
  start_time: string;
  end_time: string;
  quantity: number;
  valid_from?: string | null;
  valid_until?: string | null;
}

export interface OverrideInput {
  override_date: string;
  kind: 'closed' | 'extra' | 'reduced';
  start_time?: string | null;
  end_time?: string | null;
  quantity?: number | null;
  reason?: string | null;
}

export const capacityApi = {
  listMine(limit = 50, offset = 0) {
    return api.get<ListEnvelope<CapacityResource>>('/capacities', { limit, offset });
  },
  create(input: CapacityResourceInput) {
    return api.post<CapacityResource>('/capacities', input);
  },
  get(id: UUID) {
    return api.get<CapacityResource>(`/capacities/${id}`);
  },
  patch(id: UUID, patch: Partial<CapacityResourceInput>) {
    return api.patch<CapacityResource>(`/capacities/${id}`, patch);
  },
  listDefinitions(resourceId: UUID) {
    return api.get<ListEnvelope<CapacityDefinition>>(
      `/capacities/${resourceId}/definitions`,
    );
  },
  createDefinition(resourceId: UUID, input: DefinitionInput) {
    return api.post<CapacityDefinition>(
      `/capacities/${resourceId}/definitions`,
      input,
    );
  },
  /**
   * Availability: the day plan for one definition over one window.
   *
   * `from`/`to` are required query params and `definition_id` becomes required as
   * soon as the resource has more than one active unit — the route answers 400 with
   * the list of definitions when it cannot pick one, so callers always pass it.
   */
  listAvailability(resourceId: UUID, definitionId: UUID, from: string, to: string) {
    return api.get<AvailabilityResponse>(`/capacities/${resourceId}/availability`, {
      definition_id: definitionId,
      from,
      to,
    });
  },
  createAvailability(resourceId: UUID, input: RecurringAvailabilityInput) {
    return api.post<RecurringAvailability>(
      `/capacities/${resourceId}/availability`,
      input,
    );
  },
  deleteResource(resourceId: UUID) {
    return api.del<void>(`/capacities/${resourceId}`);
  },
  patchDefinition(definitionId: UUID, patch: Partial<DefinitionInput> & { is_active?: boolean }) {
    return api.patch<CapacityDefinition>(`/definitions/${definitionId}`, patch);
  },
  deleteDefinition(definitionId: UUID) {
    return api.del<void>(`/definitions/${definitionId}`);
  },
  createOverride(
    resourceId: UUID,
    definitionId: UUID,
    input: OverrideInput,
  ) {
    return api.post<AvailabilityOverride>(
      `/capacities/${resourceId}/availability/overrides`,
      { definition_id: definitionId, ...input },
    );
  },
  patchAvailabilityItem(id: UUID, patch: Record<string, unknown>) {
    return api.patch<RecurringAvailability | AvailabilityOverride>(
      `/availability/${id}`,
      patch,
    );
  },
  deleteAvailabilityItem(id: UUID) {
    return api.del<void>(`/availability/${id}`);
  },
  /** Server-expanded free windows for a definition (§8). */
  freeWindows(definitionId: UUID, from: string, to: string) {
    return api.get<{ items: FreeWindow[] }>('/availability/free', {
      definition_id: definitionId,
      from,
      to,
    });
  },
};

// ---------- offers / marketplace ----------

export const offerApi = {
  search(params: OfferSearchParams, signal?: AbortSignal) {
    return api.get<ListEnvelope<Offer>>('/offers', offerSearchQuery(params), signal);
  },
  get(id: UUID) {
    return api.get<Offer>(`/offers/${id}`);
  },
  create(input: OfferInput) {
    return api.post<Offer>('/offers', input);
  },
  update(id: UUID, patch: Partial<OfferInput>) {
    return api.patch<Offer>(`/offers/${id}`, patch);
  },
  publish(id: UUID) {
    return api.post<Offer>(`/offers/${id}/publish`);
  },
  pause(id: UUID) {
    return api.post<Offer>(`/offers/${id}/pause`);
  },
  close(id: UUID) {
    return api.post<Offer>(`/offers/${id}/close`);
  },
  /** `status` narrows the owner's list; closed listings are only ever visible here (§8). */
  listMine(limit = 100, offset = 0, status?: OfferStatus) {
    return api.get<ListEnvelope<Offer>>('/offers', {
      mine: true,
      status: status || undefined,
      limit,
      offset,
    });
  },
  reviews(offerId: UUID, limit = 20, offset = 0) {
    return api.get<ListEnvelope<Review>>(`/offers/${offerId}/reviews`, {
      limit,
      offset,
    });
  },
  /** Bookable windows for an offer, expanded server-side and narrowed by its quantity ceiling. */
  availability(offerId: UUID, from: string, to: string, signal?: AbortSignal) {
    return api.get<ListEnvelope<FreeWindow>>(`/offers/${offerId}/availability`,
      { from, to }, signal);
  },
};

// ---------- demands & matches ----------

export const demandApi = {
  mine(status?: string, limit = 20, offset = 0) {
    return api.get<ListEnvelope<Demand>>('/demands', { mine: true, status, limit, offset });
  },
  inbound(limit = 50, offset = 0) {
    // provider view: demands matched against own offers
    return api.get<ListEnvelope<Demand>>('/demands', { provider: true, limit, offset });
  },
  get(id: UUID) {
    return api.get<Demand>(`/demands/${id}`);
  },
  create(input: DemandInput) {
    return api.post<Demand>('/demands', input);
  },
  close(id: UUID) {
    return api.post<Demand>(`/demands/${id}/close`);
  },
  cancel(id: UUID) {
    return api.post<Demand>(`/demands/${id}/cancel`);
  },
  patch(id: UUID, patch: Partial<DemandInput>) {
    return api.patch<Demand>(`/demands/${id}`, patch);
  },
  /** Live re-scoring of the demand against offers the capacity engine would accept. */
  matches(demandId: UUID, limit = 20, offset = 0) {
    return api.get<ListEnvelope<Match>>(`/demands/${demandId}/matches`, { limit, offset });
  },
};

/**
 * Provider-side match inbox. Accepting a proposal creates the draft booking the customer
 * then confirms, so the action belongs to the provider — not to the demand's owner.
 */
export const matchApi = {
  list(status?: string, limit = 50, offset = 0) {
    return api.get<ListEnvelope<Match>>('/matches', {
      provider: true,
      status,
      limit,
      offset,
    });
  },
  get(id: UUID) {
    return api.get<Match>(`/matches/${id}`);
  },
  accept(id: UUID) {
    return api.post<Match>(`/matches/${id}/accept`);
  },
  reject(id: UUID) {
    return api.post<Match>(`/matches/${id}/reject`);
  },
};

// ---------- bookings ----------

export const bookingApi = {
  /**
   * POST /bookings/hold with Idempotency-Key + client request_fingerprint
   * (CONTRACTS §5.7). Retrying with the same hold returns the stored response.
   */
  hold(req: Omit<HoldRequest, 'request_fingerprint'>, idempotencyKey?: string) {
    const body: HoldRequest = { ...req, request_fingerprint: uuidv4() };
    return api.post<Booking>('/bookings/hold', body, {
      idempotencyKey: idempotencyKey ?? uuidv4(),
    });
  },
  /** Convert a hold (or fresh request) to a booking. */
  create(payload: { hold_id: UUID } | Omit<HoldRequest, 'request_fingerprint'>, idempotencyKey?: string) {
    return api.post<Booking>('/bookings', payload, {
      idempotencyKey: idempotencyKey ?? uuidv4(),
    });
  },
  list(scope: { mine?: boolean; provider?: boolean; status?: string }, limit = 20, offset = 0) {
    return api.get<ListEnvelope<Booking>>('/bookings', { ...scope, limit, offset });
  },
  get(id: UUID) {
    return api.get<Booking>(`/bookings/${id}`);
  },
  timeline(id: UUID, limit = 20, offset = 0) {
    return api.get<ListEnvelope<BookingStatusEvent>>(`/bookings/${id}/timeline`, {
      limit,
      offset,
    });
  },
  confirm(id: UUID) {
    return api.post<Booking>(`/bookings/${id}/confirm`);
  },
  start(id: UUID) {
    return api.post<Booking>(`/bookings/${id}/start`);
  },
  complete(id: UUID) {
    return api.post<Booking>(`/bookings/${id}/complete`);
  },
  cancel(id: UUID, reason: string) {
    return api.post<Booking>(`/bookings/${id}/cancel`, { reason });
  },
};

// ---------- orders & payments ----------

export const orderApi = {
  list(limit = 20, offset = 0) {
    return api.get<ListEnvelope<Order>>('/orders', { limit, offset });
  },
  /**
   * POST /orders — raises the invoice for a booking, optionally spending a coupon.
   * Idempotent per booking on the server, so a replay returns the first order.
   */
  create(bookingId: UUID, couponCode?: string | null) {
    return api.post<Order>('/orders', {
      booking_id: bookingId,
      coupon_code: couponCode || null,
    });
  },
  get(id: UUID) {
    return api.get<Order>(`/orders/${id}`);
  },
  payments(orderId: UUID) {
    return api.get<ListEnvelope<Payment>>(`/orders/${orderId}/payments`);
  },
};

export const paymentApi = {
  createIntent(orderId: UUID, providerKey = 'mock') {
    return api.post<Payment>('/payments/intents', {
      order_id: orderId,
      provider_key: providerKey,
    });
  },
  confirm(paymentId: UUID, idempotencyKey?: string) {
    return api.post<Payment>(`/payments/${paymentId}/confirm`, {}, {
      idempotencyKey: idempotencyKey ?? uuidv4(),
    });
  },
  get(id: UUID) {
    return api.get<Payment>(`/payments/${id}`);
  },
};

export const fulfillmentApi = {
  /** The route answers with the reloaded fulfillment, not just an ok flag. */
  addNote(fulfillmentId: UUID, body: string) {
    return api.post<{ ok: boolean; fulfillment: Fulfillment }>(
      `/fulfillments/${fulfillmentId}/notes`,
      { body },
    );
  },
};

// ---------- reviews ----------

export const reviewApi = {
  create(input: ReviewInput) {
    return api.post<Review>('/reviews', input);
  },
  reply(reviewId: UUID, reply: string) {
    return api.post<Review>(`/reviews/${reviewId}/reply`, { provider_reply: reply });
  },
  /** `provider=true` is what the caller's organization received (§8). */
  forOrg(limit = 50, offset = 0) {
    return api.get<ListEnvelope<Review>>('/reviews', { provider: true, limit, offset });
  },
  /** What the caller wrote, for the customer-side "my reviews" list. */
  mine(limit = 50, offset = 0) {
    return api.get<ListEnvelope<Review>>('/reviews', { limit, offset });
  },
  forBooking(bookingId: UUID) {
    return api.get<ListEnvelope<Review>>('/reviews', { booking_id: bookingId });
  },
  get(id: UUID) {
    return api.get<Review>(`/reviews/${id}`);
  },
  remove(id: UUID) {
    return api.post<Review>(`/reviews/${id}/remove`);
  },
  restore(id: UUID) {
    return api.post<Review>(`/reviews/${id}/restore`);
  },
};

// ---------- notifications (§1 SSE + fallback) ----------

export const notificationApi = {
  list(unreadOnly = false, limit = 30, offset = 0) {
    return api.get<ListEnvelope<AppNotification>>('/notifications', {
      unread: unreadOnly || undefined,
      limit,
      offset,
    });
  },
  markRead(id: UUID) {
    return api.post<AppNotification>(`/notifications/${id}/read`);
  },
  markAllRead() {
    return api.post<{ ok: boolean }>('/notifications/read-all');
  },
  /** SSE stream URL (CONTRACTS §1). Access token travels as query param. */
  streamUrl(accessToken: string): string {
    const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
      'http://127.0.0.1:8000/api/v1';
    return `${base.replace(/\/+$/, '')}/notifications/stream?access_token=${encodeURIComponent(accessToken)}`;
  },
};

// ---------- conversations ----------

export const conversationApi = {
  list(limit = 30, offset = 0, opts: { provider?: boolean; status?: string } = {}) {
    return api.get<ListEnvelope<Conversation>>('/conversations', {
      provider: opts.provider || undefined,
      status: opts.status || undefined,
      limit,
      offset,
    });
  },
  create(payload: { kind?: string; ref_id?: UUID | null; org_id?: UUID | null; initial_body?: string }) {
    return api.post<Conversation>('/conversations', payload);
  },
  messages(conversationId: UUID, limit = 100, offset = 0) {
    return api.get<ListEnvelope<Message>>(
      `/conversations/${conversationId}/messages`,
      { limit, offset },
    );
  },
  send(conversationId: UUID, body: string) {
    return api.post<Message>(`/conversations/${conversationId}/messages`, { body });
  },
  setStatus(conversationId: UUID, status: 'open' | 'closed' | 'archived') {
    return api.post<Conversation>(`/conversations/${conversationId}/status`, { status });
  },
};

// ---------- disputes ----------

export const disputeApi = {
  /** No scope flag: the route itself narrows to the caller (queue / org / own bookings). */
  list(opts: { provider?: boolean; status?: string; kind?: string } = {}, limit = 30, offset = 0) {
    return api.get<ListEnvelope<Dispute>>('/disputes', {
      provider: opts.provider || undefined,
      status: opts.status || undefined,
      kind: opts.kind || undefined,
      limit,
      offset,
    });
  },
  create(payload: { booking_id: UUID; kind: string; description: string }) {
    return api.post<Dispute>('/disputes', payload);
  },
  get(id: UUID) {
    return api.get<Dispute>(`/disputes/${id}`);
  },
  /** `refund_cents` only steers resolved_partial; a full refund takes the order's balance. */
  resolve(id: UUID, status: DisputeStatus, note: string, refundCents?: number | null) {
    return api.post<Dispute>(`/disputes/${id}/resolve`, {
      status,
      resolution_note: note,
      refund_cents: refundCents ?? null,
    });
  },
};

// ---------- dashboards (§8) ----------

export const dashboardApi = {
  customer() {
    return api.get<CustomerDashboard>('/dashboard/customer');
  },
  provider(from?: string, to?: string) {
    return api.get<ProviderDashboard>('/dashboard/provider', { from, to });
  },
  admin() {
    return api.get<AdminDashboard>('/dashboard/admin');
  },
};

// ---------- admin ----------

export interface AdminUserRow {
  id: UUID;
  email: string;
  full_name: string;
  roles: RoleKey[];
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
  org_names?: string[];
}

export interface OrgStaffRow {
  id: UUID;
  org_id: UUID;
  user_id: UUID;
  role: OrgStaffRole;
  status: 'active' | 'invited' | 'revoked';
  created_at: string;
  /** The list route joins the account; the invite response is the bare membership row. */
  email?: string | null;
  full_name?: string | null;
}

export const adminApi = {
  users(
    opts: { q?: string; role?: string; active?: boolean } = {},
    limit = 20,
    offset = 0,
  ) {
    return api.get<ListEnvelope<AdminUserRow>>('/admin/users', {
      q: opts.q || undefined,
      role: opts.role || undefined,
      active: opts.active,
      limit,
      offset,
    });
  },
  providers(
    opts: { q?: string; status?: string; country?: string } = {},
    limit = 20,
    offset = 0,
  ) {
    return api.get<ListEnvelope<AdminProvider>>(
      '/admin/providers',
      { q: opts.q || undefined, status: opts.status || undefined, country: opts.country || undefined, limit, offset },
    );
  },
  disputes(
    opts: { status?: string; kind?: string; oldestFirst?: boolean } = {},
    limit = 20,
    offset = 0,
  ) {
    return api.get<ListEnvelope<AdminDisputeRow>>('/admin/disputes', {
      status: opts.status || undefined,
      kind: opts.kind || undefined,
      oldest_first: opts.oldestFirst || undefined,
      limit,
      offset,
    });
  },
  auditLogs(
    opts: { action?: string; entityType?: string; from?: string; to?: string } = {},
    limit = 30,
    offset = 0,
  ) {
    return api.get<ListEnvelope<AuditLog>>('/admin/audit-logs', {
      action: opts.action || undefined,
      entity_type: opts.entityType || undefined,
      from: opts.from || undefined,
      to: opts.to || undefined,
      limit,
      offset,
    });
  },
  categories() {
    return api.get<ListEnvelope<CapacityCategory>>('/admin/categories', { limit: 100 });
  },
  patchCategory(id: UUID, patch: { label?: string; is_active?: boolean }) {
    return api.patch<CapacityCategory>(`/admin/categories/${id}`, patch);
  },
  promotions(opts: { q?: string; status?: string; kind?: string } = {}, limit = 50, offset = 0) {
    return api.get<ListEnvelope<Promotion>>('/admin/promotions', {
      q: opts.q || undefined,
      status: opts.status || undefined,
      kind: opts.kind || undefined,
      limit,
      offset,
    });
  },
  createPromotion(payload: Record<string, unknown>) {
    return api.post<Promotion>('/admin/promotions', payload);
  },
  patchPromotion(id: UUID, patch: Record<string, unknown>) {
    return api.patch<Promotion>(`/admin/promotions/${id}`, patch);
  },
  promotionRedemptions(id: UUID, limit = 50, offset = 0) {
    return api.get<ListEnvelope<PromotionRedemption>>(
      `/admin/promotions/${id}/redemptions`,
      { limit, offset },
    );
  },
  /** A grant answers with the membership row it wrote (§5.1); a revocation answers 204. */
  grantRole(userId: UUID, role: Exclude<RoleKey, 'customer'>) {
    return api.post<UserRole>(`/admin/users/${userId}/roles`, { role });
  },
  revokeRole(userId: UUID, role: Exclude<RoleKey, 'customer'>) {
    return api.del<void>(`/admin/users/${userId}/roles/${role}`);
  },
  analytics(from?: string, to?: string) {
    return api.get<PlatformAnalytics>('/admin/analytics', { from, to });
  },
};

// ---------- assistant (§8) ----------

/**
 * The `/ai/*` surface is advisory and read-only: no call here writes a row, and none of
 * them is on the booking path. Each one answers from data this platform already stores,
 * so a failure shows an empty suggestion rather than a broken feature.
 */
export interface PriceSuggestInput {
  offer_id?: UUID;
  category_key?: string;
  city?: string;
  country?: string;
  capacity_mode?: 'scheduled' | 'quantity' | 'open_ended';
  unit_label?: string;
  currency?: CurrencyCode;
}

export const aiApi = {
  parseSearch(text: string) {
    return api.post<ParsedQuery>('/ai/parse-search', { text });
  },
  draftListing(rawText: string, opts: { category_key?: string; currency?: CurrencyCode } = {}) {
    return api.post<ListingDraft>('/ai/listing-draft', {
      raw_text: rawText,
      category_key: opts.category_key || undefined,
      currency: opts.currency ?? 'USD',
    });
  },
  priceSuggest(input: PriceSuggestInput) {
    return api.post<PriceSuggestion>('/ai/price-suggest', input);
  },
  utilizationInsights(from?: string, to?: string, orgId?: UUID) {
    return api.get<UtilizationInsights>('/ai/utilization-insights', {
      from,
      to,
      org_id: orgId,
    });
  },
  copilot(orgId?: UUID) {
    return api.get<CopilotBriefing>('/ai/copilot', { org_id: orgId });
  },
};
