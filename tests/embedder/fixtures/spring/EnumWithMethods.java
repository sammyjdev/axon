package com.example.model;
public enum EnumWithMethods {
    PENDING {
        @Override public String display() { return "Pending Review"; }
        @Override public boolean isTerminal() { return false; }
    },
    APPROVED {
        @Override public String display() { return "Approved"; }
        @Override public boolean isTerminal() { return false; }
    },
    REJECTED {
        @Override public String display() { return "Rejected"; }
        @Override public boolean isTerminal() { return true; }
    },
    COMPLETED {
        @Override public String display() { return "Completed"; }
        @Override public boolean isTerminal() { return true; }
    };
    public abstract String display();
    public abstract boolean isTerminal();
    public boolean canTransitionTo(EnumWithMethods next) {
        if (this.isTerminal()) return false;
        return next != this;
    }
}
