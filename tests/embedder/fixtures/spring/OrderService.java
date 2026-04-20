package com.example.demo.service;

import org.springframework.cache.annotation.CacheEvict;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.*;
import java.util.stream.Collectors;

/**
 * OrderService handles the full lifecycle of an order:
 * creation, payment, fulfillment, shipping, delivery, cancellation.
 */
@Service
public class OrderService {

    private final OrderRepository orderRepository;
    private final ProductRepository productRepository;
    private final PaymentService paymentService;
    private final InventoryService inventoryService;
    private final NotificationService notificationService;
    private final AuditService auditService;

    public OrderService(
            OrderRepository orderRepository,
            ProductRepository productRepository,
            PaymentService paymentService,
            InventoryService inventoryService,
            NotificationService notificationService,
            AuditService auditService) {
        this.orderRepository = orderRepository;
        this.productRepository = productRepository;
        this.paymentService = paymentService;
        this.inventoryService = inventoryService;
        this.notificationService = notificationService;
        this.auditService = auditService;
    }

    @Transactional
    public Order create(Long customerId, List<OrderItem> items) {
        validateItems(items);
        reserveInventory(items);

        Order order = Order.builder()
                .customerId(customerId)
                .items(items)
                .status(OrderStatus.PENDING)
                .createdAt(Instant.now())
                .totalValue(calculateTotal(items))
                .build();

        Order saved = orderRepository.save(order);
        auditService.log("Order", saved.getId(), "CREATED", "system", null);
        notificationService.sendOrderConfirmation(saved.getId(), customerId.toString());
        return saved;
    }

    @Cacheable(cacheNames = "orders", key = "#id")
    public Order findById(Long id) {
        return orderRepository.findById(id)
                .orElseThrow(() -> new OrderNotFoundException(id));
    }

    @Transactional(readOnly = true)
    public List<Order> findByCustomer(Long customerId) {
        return orderRepository.findByCustomerId(customerId);
    }

    @Transactional(readOnly = true)
    public List<Order> findByStatus(String status) {
        return orderRepository.findByStatus(status);
    }

    @Transactional
    @CacheEvict(cacheNames = "orders", key = "#orderId")
    public Order processPayment(Long orderId, String paymentMethod, double amount) {
        Order order = findById(orderId);

        if (order.getStatus() != OrderStatus.PENDING) {
            throw new IllegalStateException("Order " + orderId + " is not in PENDING status");
        }

        PaymentService.PaymentResult result = paymentService.processPayment(orderId, amount, "USD");

        if (!result.success()) {
            order.setStatus(OrderStatus.PAYMENT_FAILED);
            order.setFailureReason(result.message());
            orderRepository.save(order);
            notificationService.sendPaymentFailed(orderId, customerId(order));
            return order;
        }

        order.setStatus(OrderStatus.PROCESSING);
        order.setTransactionId(result.transactionId());
        order.setPaymentAt(Instant.now());
        Order saved = orderRepository.save(order);
        auditService.log("Order", orderId, "PAYMENT_PROCESSED", "system", result.transactionId());
        return saved;
    }

    @Transactional
    @CacheEvict(cacheNames = "orders", key = "#orderId")
    public Order fulfil(Long orderId) {
        Order order = findById(orderId);

        if (order.getStatus() != OrderStatus.PROCESSING) {
            throw new IllegalStateException("Cannot fulfil order in status: " + order.getStatus());
        }

        for (OrderItem item : order.getItems()) {
            inventoryService.deduct(item.getProductId(), item.getQuantity());
        }

        order.setStatus(OrderStatus.FULFILLED);
        order.setFulfilledAt(Instant.now());
        Order saved = orderRepository.save(order);
        auditService.log("Order", orderId, "FULFILLED", "warehouse", null);
        return saved;
    }

    @Transactional
    @CacheEvict(cacheNames = "orders", key = "#orderId")
    public Order ship(Long orderId, String trackingNumber, String carrier) {
        Order order = findById(orderId);

        if (order.getStatus() != OrderStatus.FULFILLED) {
            throw new IllegalStateException("Cannot ship order in status: " + order.getStatus());
        }

        order.setStatus(OrderStatus.SHIPPED);
        order.setTrackingNumber(trackingNumber);
        order.setCarrier(carrier);
        order.setShippedAt(Instant.now());

        Order saved = orderRepository.save(order);
        notificationService.sendShipmentNotification(orderId, customerId(order), trackingNumber);
        auditService.log("Order", orderId, "SHIPPED", "logistics", trackingNumber);
        return saved;
    }

    @Transactional
    @CacheEvict(cacheNames = "orders", key = "#orderId")
    public Order deliver(Long orderId) {
        Order order = findById(orderId);

        if (order.getStatus() != OrderStatus.SHIPPED) {
            throw new IllegalStateException("Cannot deliver order in status: " + order.getStatus());
        }

        order.setStatus(OrderStatus.DELIVERED);
        order.setDeliveredAt(Instant.now());
        Order saved = orderRepository.save(order);
        auditService.log("Order", orderId, "DELIVERED", "system", null);
        notificationService.sendDeliveryConfirmation(orderId, customerId(order));
        return saved;
    }

    @Transactional
    @CacheEvict(cacheNames = "orders", key = "#orderId")
    public Order cancel(Long orderId, String reason) {
        Order order = findById(orderId);

        if (!order.getStatus().isCancellable()) {
            throw new IllegalStateException("Cannot cancel order in status: " + order.getStatus());
        }

        releaseInventory(order.getItems());

        if (order.getTransactionId() != null) {
            paymentService.refund(order.getTransactionId(), order.getTotalValue());
        }

        order.setStatus(OrderStatus.CANCELLED);
        order.setCancelledAt(Instant.now());
        order.setCancelReason(reason);
        Order saved = orderRepository.save(order);
        auditService.log("Order", orderId, "CANCELLED", "user", reason);
        notificationService.sendCancellationNotification(orderId, customerId(order));
        return saved;
    }

    @Transactional(readOnly = true)
    public Map<String, Long> countByStatus() {
        return Arrays.stream(OrderStatus.values())
                .collect(Collectors.toMap(
                        OrderStatus::name,
                        s -> orderRepository.countByStatus(s.name())
                ));
    }

    @Transactional(readOnly = true)
    public OrderSummary buildSummary(Long customerId) {
        List<Order> orders = findByCustomer(customerId);

        long total = orders.size();
        long completed = orders.stream()
                .filter(o -> o.getStatus() == OrderStatus.DELIVERED)
                .count();
        double totalSpent = orders.stream()
                .filter(o -> o.getStatus() == OrderStatus.DELIVERED)
                .mapToDouble(Order::getTotalValue)
                .sum();
        OptionalDouble avgValue = orders.stream()
                .mapToDouble(Order::getTotalValue)
                .average();

        return new OrderSummary(customerId, total, completed, totalSpent, avgValue.orElse(0.0));
    }

    @Transactional(readOnly = true)
    public List<Order> findLateOrders(int daysThreshold) {
        Instant cutoff = Instant.now().minusSeconds((long) daysThreshold * 86400);
        return orderRepository.findByStatus(OrderStatus.PROCESSING.name()).stream()
                .filter(o -> o.getCreatedAt().isBefore(cutoff))
                .collect(Collectors.toList());
    }

    @Transactional
    public void processLateOrdersBatch(int daysThreshold) {
        List<Order> lateOrders = findLateOrders(daysThreshold);
        lateOrders.forEach(order -> {
            try {
                notificationService.sendLateOrderAlert(order.getId(), customerId(order));
                auditService.log("Order", order.getId(), "LATE_ALERT_SENT", "scheduler", null);
            } catch (Exception e) {
                System.err.println("Failed to alert for order " + order.getId() + ": " + e.getMessage());
            }
        });
    }

    @Transactional(readOnly = true)
    public Map<String, Double> revenueByCategory(Instant from, Instant to) {
        return orderRepository.findByCreatedAtBetweenAndStatus(from, to, OrderStatus.DELIVERED.name())
                .stream()
                .flatMap(o -> o.getItems().stream())
                .collect(Collectors.groupingBy(
                        item -> productRepository.findById(item.getProductId())
                                .map(Product::getCategory)
                                .orElse("unknown"),
                        Collectors.summingDouble(item -> item.getPrice() * item.getQuantity())
                ));
    }

    @Transactional
    public Order addItem(Long orderId, Long productId, int quantity) {
        Order order = findById(orderId);

        if (order.getStatus() != OrderStatus.PENDING) {
            throw new IllegalStateException("Cannot add items to order in status: " + order.getStatus());
        }

        Product product = productRepository.findById(productId)
                .orElseThrow(() -> new RuntimeException("Product not found: " + productId));

        inventoryService.reserve(productId, quantity);

        OrderItem item = new OrderItem(null, orderId, productId, quantity, product.getPrice());
        order.getItems().add(item);
        order.setTotalValue(calculateTotal(order.getItems()));
        return orderRepository.save(order);
    }

    @Transactional
    public Order removeItem(Long orderId, Long itemId) {
        Order order = findById(orderId);

        if (order.getStatus() != OrderStatus.PENDING) {
            throw new IllegalStateException("Cannot remove items from order in status: " + order.getStatus());
        }

        OrderItem item = order.getItems().stream()
                .filter(i -> i.getId().equals(itemId))
                .findFirst()
                .orElseThrow(() -> new RuntimeException("Item not found: " + itemId));

        inventoryService.release(item.getProductId(), item.getQuantity());
        order.getItems().remove(item);
        order.setTotalValue(calculateTotal(order.getItems()));
        return orderRepository.save(order);
    }

    @Transactional(readOnly = true)
    public boolean canAcceptMoreItems(Long orderId) {
        Order order = findById(orderId);
        return order.getStatus() == OrderStatus.PENDING
                && order.getItems().size() < 50;
    }

    @Transactional(readOnly = true)
    public List<Product> getOrderedProducts(Long orderId) {
        Order order = findById(orderId);
        return order.getItems().stream()
                .map(item -> productRepository.findById(item.getProductId()).orElse(null))
                .filter(Objects::nonNull)
                .collect(Collectors.toList());
    }

    record OrderSummary(Long customerId, long total, long completed, double totalSpent, double avgValue) {}

    private void validateItems(List<OrderItem> items) {
        if (items == null || items.isEmpty()) {
            throw new IllegalArgumentException("Order must have at least one item");
        }
        items.forEach(item -> {
            if (item.getQuantity() <= 0) {
                throw new IllegalArgumentException("Quantity must be positive for product " + item.getProductId());
            }
        });
    }

    private void reserveInventory(List<OrderItem> items) {
        items.forEach(item -> inventoryService.reserve(item.getProductId(), item.getQuantity()));
    }

    private void releaseInventory(List<OrderItem> items) {
        items.forEach(item -> inventoryService.release(item.getProductId(), item.getQuantity()));
    }

    private double calculateTotal(List<OrderItem> items) {
        return items.stream()
                .mapToDouble(item -> item.getPrice() * item.getQuantity())
                .sum();
    }

    private Long customerId(Order order) {
        return order.getCustomerId();
    }
}
