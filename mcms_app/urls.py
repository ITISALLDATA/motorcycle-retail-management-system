from django.contrib.auth import views as auth_views 
from django.urls import path
from . import views


urlpatterns = [
    # Deposits
    path('deposits/', views.DepositListView.as_view(), name='deposit_list'), #
    path('deposits/create/', views.add_deposit, name='deposit_create'), #
    path('deposits/<int:pk>/', views.DepositDetailView.as_view(), name='deposit_detail'), #
    path('deposits/<int:deposit_id>/edit/', views.edit_deposit, name='deposit_edit'), #
    path('deposits/<int:deposit_id>/cancel/', views.cancel_deposit, name='deposit_cancel'), #

    # Withdrawals
    path('withdrawals/', views.WithdrawalListView.as_view(), name='withdrawal_list'), #
    path('withdrawals/create/', views.add_withdrawal, name='withdrawal_create'), #
    path('withdrawals/<int:pk>/', views.WithdrawalDetailView.as_view(), name='withdrawal_detail'), #
    path('withdrawals/<int:withdrawal_id>/edit/', views.edit_withdrawal, name='withdrawal_edit'), #
    path('withdrawals/<int:withdrawal_id>/cancel/', views.cancel_withdrawal, name='withdrawal_cancel'), #

    # Motorcycles
    path('motorcycles/', views.MotorcycleListView.as_view(), name='motorcycle_list'),
    path('motorcycles/create/', views.add_motorcycle, name='motorcycle_create'),
    path('motorcycles/<int:pk>/', views.MotorcycleDetailView.as_view(), name='motorcycle_detail'),
    path('motorcycles/<int:pk>/edit/', views.edit_motorcycle, name='motorcycle_edit'),
    path('motorcycles/<int:pk>/discontinue/', views.motorcycle_discontinue_view, name='motorcycle_discontinue_confirm'),
    path('motorcycles/<int:pk>/delete/', views.motorcycle_delete_permanently_view, name='motorcycle_delete_confirm'), # For permanent delete

    # Customers
    path('customers/create/', views.customer_create, name='customer_create'), #
    path('customers/', views.CustomerListView.as_view(), name='customer_list'), #
    path('customers/<int:pk>/edit/', views.customer_edit, name='customer_edit'), #
    path('customers/<int:pk>/', views.CustomerDetailView.as_view(), name='customer_detail'),  #
    
    # Loan Repayments
    path('loans/', views.LoanListView.as_view(), name='loan_list'), # Changed from view_loans
    path('loans/create/', views.add_loan, name='loan_create'),
    path('loans/<int:pk>/', views.LoanDetailView.as_view(), name='loan_detail'), # New detail view
    path('loans/<int:loan_id>/edit/', views.edit_loan, name='loan_edit'),
    path('loans/<int:loan_id>/cancel/', views.cancel_loan, name='loan_cancel_confirm'), # Changed name for clarity

    # Loan Repayments
    path('loan-repayments/', views.LoanRepaymentListView.as_view(), name='loan_repayment_list'), # Changed path & name
    path('loan-repayments/create/', views.add_loan_repayment, name='loan_repayment_create'), # Changed name
    path('loan-repayments/<int:pk>/', views.LoanRepaymentDetailView.as_view(), name='loan_repayment_detail'), # New
    path('loan-repayments/<int:repayment_id>/edit/', views.edit_loan_repayment, name='loan_repayment_edit'), # Changed name
    path('loan-repayments/<int:repayment_id>/delete/', views.delete_loan_repayment, name='loan_repayment_delete_confirm'), # Changed name

    # Supplier URLs
    path('suppliers/', views.SupplierListView.as_view(), name='supplier_list'), #
    path('suppliers/create/', views.supplier_create, name='supplier_create'), #
    path('suppliers/<int:pk>/', views.SupplierDetailView.as_view(), name='supplier_detail'), #
    path('suppliers/<int:pk>/edit/', views.supplier_edit, name='supplier_edit'), #
    
    # Payment URLs
    path('payments/', views.PaymentListView.as_view(), name='payment_list'), #
    path('payments/create/', views.payment_create, name='payment_create'), #
    path('payments/<int:pk>/', views.PaymentDetailView.as_view(), name='payment_detail'), #
    path('payments/<int:pk>/edit/', views.payment_edit, name='payment_edit'), #
    path('payments/<int:pk>/cancel/', views.payment_cancel, name='payment_cancel'), #
    
    # Delivery URLs
    path('deliveries/', views.DeliveryListView.as_view(), name='delivery_list'), #
    path('deliveries/create/', views.delivery_create, name='delivery_create'), #
    path('deliveries/<int:pk>/', views.DeliveryDetailView.as_view(), name='delivery_detail'), #
    path('deliveries/<int:pk>/cancel/', views.delivery_cancel, name='delivery_cancel'), #
    
    # Inventory URLs
    path('inventory/', views.InventoryListView.as_view(), name='inventory_list'), #
    path('inventory/<int:pk>/', views.InventoryDetailView.as_view(), name='inventory_detail'), #
    
    # AJAX URLs
    path('ajax/payment-items/<int:payment_id>/', views.get_payment_items, name='get_payment_items'), #
    path('ajax/validate-payment-total/', views.validate_payment_total, name='validate_payment_total'), #

    # Sales URLs
    path('sales/', views.SaleListView.as_view(), name='sale_list'), #
    path('sales/create/', views.sale_create_view, name='sale_create'), #
    path('sales/<int:pk>/', views.SaleDetailView.as_view(), name='sale_detail'), #
    path('sales/<int:pk>/edit/', views.sale_edit_view, name='sale_edit'), #
    path('sales/<int:pk>/cancel/', views.sale_cancel_view, name='sale_cancel_confirm'), #

    path('login/', auth_views.LoginView.as_view(
            template_name='mcms_app/auth/login.html'
         ), name='login'),
    path('logout/', auth_views.LogoutView.as_view(
            template_name = 'mcms_app/auth/logout_custom.html'
    ), name='logout'),

    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
]

