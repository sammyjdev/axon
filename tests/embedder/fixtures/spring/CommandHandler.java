package com.example.handler;

public interface CommandHandler<C, R> {
    R handle(C command);

    default R handleWithLogging(C command) {
        System.out.println("Handling: " + command);
        R result = handle(command);
        System.out.println("Result: " + result);
        return result;
    }

    default CommandHandler<C, R> withFallback(R fallback) {
        return cmd -> {
            try { return handle(cmd); }
            catch (Exception e) { return fallback; }
        };
    }

    static <C, R> CommandHandler<C, R> noOp() {
        return cmd -> null;
    }
}
