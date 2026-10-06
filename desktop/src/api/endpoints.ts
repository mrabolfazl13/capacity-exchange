// Typed endpoint functions for the full CONTRACTS §8 surface. Every feature
// module goes through this file — no ad-hoc fetch calls in components — so the
// client boundary is a single mockable seam and paths/queries stay consistent.

import { api, uuidv4 } from '@/api/client';
import type {
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
  CustomerDashboard,
  Demand,
  DemandInput,
  Dispute,
  DisputeStatus,
  FreeWindow,
  Fulfillment,
  HoldRequest,
  ListEnvelope,
  Match,
  Message,
  Offer,
  OfferInput,
  OfferSearchParams,
  Order,
  Organization,
  OrgStaffRole,
  Payment,
  PlatformAnalytics,
  Promotion,
  ProviderDashboard,
  RecurringAvailability,
  Review,
  ReviewInput,
  RoleKey,
  TokenPair,
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
    line1?: string;
    line2?: string;
    city?: string;
    state?: string;
    postal_code?: string;
    country?: string;
  };
  lat?: number | null;
  lon?: number | null;
  timezone?: string;
  attributes?: Record<string, unknown>;
  photos?: { url: string; caption?: string; sort_order?: number }[];
  documents?: { name: string; url: string; kind?: string }[];
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
  listMine(limit = 100, offset = 0) {
    return api.get<ListEnvelope<Offer>>('/offers', { mine: true, limit, offset });
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
  matches(demandId: UUID) {
    return api.get<ListEnvelope<Match>>(`/demands/${demandId}/matches`);
  },
  acceptMatch(matchId: UUID) {
    return api.post<Match>(`/matches/${matchId}/accept`);
  },
  rejectMatch(matchId: UUID) {
    return api.post<Match>(`/matches/${matchId}/reject`);
  },
};

/** Provider-side match inbox: proposals across every demand that touched our offers. */
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
  timeline(id: UUID) {
    return api.get<ListEnvelope<BookingStatusEvent>>(`/bookings/${id}/timeline`);
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
    return api.patch<Review>(`/reviews/${reviewId}/reply`, { provider_reply: reply });
  },
  forOrg(limit = 50, offset = 0) {
    return api.get<ListEnvelope<Review>>('/reviews', { org: true, limit, offset });
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
  list(limit = 30, offset = 0) {
    return api.get<ListEnvelope<Conversation>>('/conversations', { limit, offset });
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
  list(limit = 30, offset = 0) {
    return api.get<ListEnvelope<Dispute>>('/disputes', { limit, offset });
  },
  create(payload: { booking_id: UUID; kind: string; description: string }) {
    return api.post<Dispute>('/disputes', payload);
  },
  get(id: UUID) {
    return api.get<Dispute>(`/disputes/${id}`);
  },
  resolve(id: UUID, status: DisputeStatus, note: string) {
    return api.post<Dispute>(`/disputes/${id}/resolve`, {
      status,
      resolution_note: note,
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
  user?: User | null;
}

export const adminApi = {
  users(q?: string, limit = 20, offset = 0) {
    return api.get<ListEnvelope<AdminUserRow>>('/admin/users', { q, limit, offset });
  },
  providers(q?: string, limit = 20, offset = 0) {
    return api.get<ListEnvelope<import('../types/api').AdminProvider>>(
      '/admin/providers',
      { q, limit, offset },
    );
  },
  disputes(status?: string, limit = 20, offset = 0) {
    return api.get<ListEnvelope<Dispute>>('/admin/disputes', { status, limit, offset });
  },
  auditLogs(entityType?: string, limit = 30, offset = 0) {
    return api.get<ListEnvelope<AuditLog>>('/admin/audit-logs', {
      entity_type: entityType,
      limit,
      offset,
    });
  },
  categories() {
    return api.get<ListEnvelope<CapacityCategory>>('/admin/categories');
  },
  patchCategory(id: UUID, patch: { label?: string; is_active?: boolean }) {
    return api.patch<CapacityCategory>(`/admin/categories/${id}`, patch);
  },
  promotions(limit = 50, offset = 0) {
    return api.get<ListEnvelope<Promotion>>('/admin/promotions', { limit, offset });
  },
  createPromotion(payload: Record<string, unknown>) {
    return api.post<Promotion>('/admin/promotions', payload);
  },
  patchPromotion(id: UUID, patch: Record<string, unknown>) {
    return api.patch<Promotion>(`/admin/promotions/${id}`, patch);
  },
  promotionRedemptions(id: UUID, limit = 50, offset = 0) {
    return api.get<ListEnvelope<Record<string, unknown>>>(
      `/admin/promotions/${id}/redemptions`,
      { limit, offset },
    );
  },
  grantRole(userId: UUID, role: RoleKey) {
    return api.post<{ ok: boolean; roles: RoleKey[] }>(`/admin/users/${userId}/roles`, {
      role,
    });
  },
  revokeRole(userId: UUID, role: RoleKey) {
    return api.del<{ ok: boolean; roles: RoleKey[] }>(`/admin/users/${userId}/roles/${role}`);
  },
  analytics(from?: string, to?: string) {
    return api.get<PlatformAnalytics>('/admin/analytics', { from, to });
  },
};
