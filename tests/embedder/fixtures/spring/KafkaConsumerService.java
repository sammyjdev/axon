package com.example.demo.messaging;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;

@Component
public class KafkaConsumerService {

    private final OrderService orderService;
    private final NotificationService notificationService;

    public KafkaConsumerService(OrderService orderService, NotificationService notificationService) {
        this.orderService = orderService;
        this.notificationService = notificationService;
    }

    @KafkaListener(topics = "order-created", groupId = "order-processor")
    public void onOrderCreated(String message, Acknowledgment ack) {
        try {
            OrderEvent event = OrderEvent.parse(message);
            orderService.processNew(event.orderId());
            ack.acknowledge();
        } catch (Exception e) {
            System.err.println("Failed to process order-created: " + e.getMessage());
        }
    }

    @KafkaListener(topics = "payment-completed", groupId = "order-processor")
    public void onPaymentCompleted(String message, Acknowledgment ack) {
        PaymentEvent event = PaymentEvent.parse(message);
        orderService.markPaid(event.orderId(), event.transactionId());
        notificationService.sendOrderConfirmation(event.orderId(), event.customerEmail());
        ack.acknowledge();
    }

    @KafkaListener(topics = "order-cancelled", groupId = "order-processor")
    public void onOrderCancelled(String message, Acknowledgment ack) {
        CancelEvent event = CancelEvent.parse(message);
        orderService.cancel(event.orderId(), event.reason());
        ack.acknowledge();
    }
}
