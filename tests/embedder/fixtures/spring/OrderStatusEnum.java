package com.example.model;

public enum OrderStatusEnum {
    PENDING, CONFIRMED, PROCESSING, SHIPPED, DELIVERED, CANCELLED;

    public String getLabel() {
        return name().charAt(0) + name().substring(1).toLowerCase();
    }

    public boolean canTransitionTo(OrderStatusEnum next) {
        return switch (this) {
            case PENDING -> next == CONFIRMED || next == CANCELLED;
            case CONFIRMED -> next == PROCESSING || next == CANCELLED;
            case PROCESSING -> next == SHIPPED;
            case SHIPPED -> next == DELIVERED;
            default -> false;
        };
    }
}
