from django.urls import path
from .views import (
    MerchantListView,
    MerchantDashboardView,
    PayoutListView,
    PayoutDetailView,
    CreatePayoutView,
)

urlpatterns = [
    path("merchants/", MerchantListView.as_view(), name="merchant-list"),
    path("merchants/<uuid:merchant_id>/", MerchantDashboardView.as_view(), name="merchant-dashboard"),
    path("payouts/", CreatePayoutView.as_view(), name="payout-create"),
    path("payouts/list/", PayoutListView.as_view(), name="payout-list"),
    path("payouts/<uuid:payout_id>/", PayoutDetailView.as_view(), name="payout-detail"),
]
