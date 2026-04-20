package com.example.service;

import org.springframework.stereotype.Service;

@Service
public class PaymentService {
    public String processPayment(String orderId, double amount) {
        return "paid:" + orderId + ":" + amount;
    }

    public String refund(String paymentId) {
        return "refunded:" + paymentId;
    }

    public boolean validate(String cardNumber) {
        return cardNumber != null && cardNumber.length() == 16;
    }

    record PaymentResult(String paymentId, String status, double amount) {}
}
