package com.example.service;

import org.springframework.stereotype.Service;

@Service
public class NotificationService {
    private final java.util.concurrent.ExecutorService executor;

    public NotificationService(java.util.concurrent.ExecutorService executor) {
        this.executor = executor;
    }

    public void sendAsync(String recipient, String message) {
        executor.submit(new Runnable() {
            @Override
            public void run() {
                System.out.println("Sending to " + recipient + ": " + message);
            }
        });
    }

    public void sendOrderConfirmation(String orderId, String email) {
        String body = buildBody(orderId);
        executor.submit(new Runnable() {
            @Override
            public void run() {
                System.out.println("Confirm " + orderId + " to " + email + ": " + body);
            }
        });
    }

    public void sendBulk(java.util.List<String> recipients, String message) {
        recipients.forEach(r -> sendAsync(r, message));
    }

    private String buildBody(String orderId) {
        return "Order " + orderId + " confirmed";
    }
}
