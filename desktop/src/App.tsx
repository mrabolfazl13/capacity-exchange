// Route table. Every path the sidebar links to resolves here, and every route is
// guarded by the role the screen's actions actually require (§4).

import { Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from '@/components/AppLayout';
import { RequireAuth, RequireRole } from '@/routes/guards';
import { LoginPage } from '@/features/auth/pages/LoginPage';
import { RegisterPage } from '@/features/auth/pages/RegisterPage';
import { SearchPage } from '@/features/marketplace/pages/SearchPage';
import { OfferDetailPage } from '@/features/marketplace/pages/OfferDetailPage';
import { BookingFlowPage } from '@/features/booking/pages/BookingFlowPage';
import { BookingsPage } from '@/features/booking/pages/BookingsPage';
import { BookingDetailPage } from '@/features/booking/pages/BookingDetailPage';
import { DashboardPage } from '@/features/dashboard/pages/DashboardPage';
import { DemandsPage } from '@/features/demands/pages/DemandsPage';
import { DemandDetailPage } from '@/features/demands/pages/DemandDetailPage';
import { MessagesPage } from '@/features/messages/pages/MessagesPage';
import { NotificationsPage } from '@/features/notifications/pages/NotificationsPage';
import { ProviderDashboardPage } from '@/features/provider/pages/ProviderDashboardPage';
import { ProviderResourcesPage } from '@/features/provider/pages/ProviderResourcesPage';
import { ProviderOffersPage } from '@/features/provider/pages/ProviderOffersPage';
import { ProviderMatchesPage } from '@/features/provider/pages/ProviderMatchesPage';
import { ProviderBookingsPage } from '@/features/provider/pages/ProviderBookingsPage';
import { ProviderReviewsPage } from '@/features/provider/pages/ProviderReviewsPage';
import { CapacityWizardPage } from '@/features/provider/pages/CapacityWizardPage';
import { AdminUsersPage } from '@/features/admin/pages/AdminUsersPage';
import { AdminProvidersPage } from '@/features/admin/pages/AdminProvidersPage';
import { AdminDisputesPage } from '@/features/admin/pages/AdminDisputesPage';
import { AdminAuditPage } from '@/features/admin/pages/AdminAuditPage';
import { AdminCategoriesPage } from '@/features/admin/pages/AdminCategoriesPage';
import { AdminPromotionsPage } from '@/features/admin/pages/AdminPromotionsPage';
import { NotFoundPage } from '@/routes/NotFoundPage';

const PROVIDER_ROLES = ['provider', 'org_admin'] as const;
/** The dispute queue is the one admin screen support may reach; the rest are platform-only. */
const DISPUTE_ROLES = ['platform_admin', 'support'] as const;
const ADMIN_ROLES = ['platform_admin'] as const;

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />

      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/marketplace" element={<SearchPage />} />
        <Route path="/offers/:offerId" element={<OfferDetailPage />} />
        <Route path="/offers/:offerId/book" element={<BookingFlowPage />} />
        <Route path="/bookings" element={<BookingsPage />} />
        <Route path="/bookings/:bookingId" element={<BookingDetailPage />} />
        <Route path="/demands" element={<DemandsPage />} />
        <Route path="/demands/:demandId" element={<DemandDetailPage />} />
        <Route path="/messages" element={<MessagesPage />} />
        <Route path="/messages/:conversationId" element={<MessagesPage />} />
        <Route path="/notifications" element={<NotificationsPage />} />

        <Route
          path="/provider"
          element={
            <RequireRole roles={[...PROVIDER_ROLES]}>
              <ProviderDashboardPage />
            </RequireRole>
          }
        />
        <Route
          path="/provider/resources"
          element={
            <RequireRole roles={[...PROVIDER_ROLES]}>
              <ProviderResourcesPage />
            </RequireRole>
          }
        />
        <Route
          path="/provider/offers"
          element={
            <RequireRole roles={[...PROVIDER_ROLES]}>
              <ProviderOffersPage />
            </RequireRole>
          }
        />
        <Route
          path="/provider/matches"
          element={
            <RequireRole roles={[...PROVIDER_ROLES]}>
              <ProviderMatchesPage />
            </RequireRole>
          }
        />
        <Route
          path="/provider/bookings"
          element={
            <RequireRole roles={[...PROVIDER_ROLES]}>
              <ProviderBookingsPage />
            </RequireRole>
          }
        />
        <Route
          path="/provider/reviews"
          element={
            <RequireRole roles={[...PROVIDER_ROLES]}>
              <ProviderReviewsPage />
            </RequireRole>
          }
        />
        <Route
          path="/provider/new"
          element={
            <RequireRole roles={[...PROVIDER_ROLES]}>
              <CapacityWizardPage />
            </RequireRole>
          }
        />

        <Route
          path="/admin/users"
          element={
            <RequireRole roles={[...ADMIN_ROLES]}>
              <AdminUsersPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/providers"
          element={
            <RequireRole roles={[...ADMIN_ROLES]}>
              <AdminProvidersPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/disputes"
          element={
            <RequireRole roles={[...DISPUTE_ROLES]}>
              <AdminDisputesPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/audit-logs"
          element={
            <RequireRole roles={[...ADMIN_ROLES]}>
              <AdminAuditPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/categories"
          element={
            <RequireRole roles={[...ADMIN_ROLES]}>
              <AdminCategoriesPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/promotions"
          element={
            <RequireRole roles={[...ADMIN_ROLES]}>
              <AdminPromotionsPage />
            </RequireRole>
          }
        />

        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
