package com.example.service;
import org.springframework.stereotype.Service;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Classe grande com 500+ linhas simulando um serviço complexo de e-commerce.
 * Cobre: métodos longos, aninhamento, inner logic.
 */
@Service
public class LargeServiceClass {
    private final Map<String, Object> cache = new HashMap<>();
    private final List<String> log = new ArrayList<>();

    // --- Inventory Methods ---
    public void addProduct(String id, String name, int qty, double price) {
        cache.put("prod:" + id, Map.of("name", name, "qty", qty, "price", price));
        logEvent("ADD_PRODUCT", id);
    }
    public Optional<Map<String, Object>> getProduct(String id) {
        Object v = cache.get("prod:" + id);
        return v instanceof Map ? Optional.of((Map<String, Object>) v) : Optional.empty();
    }
    public boolean updateQuantity(String id, int delta) {
        return getProduct(id).map(p -> {
            int current = (Integer) p.get("qty");
            int newQty = current + delta;
            if (newQty < 0) return false;
            cache.put("prod:" + id, new HashMap<>(p) {{ put("qty", newQty); }});
            logEvent("UPDATE_QTY", id + ":" + newQty);
            return true;
        }).orElse(false);
    }
    public void removeProduct(String id) {
        cache.remove("prod:" + id);
        logEvent("REMOVE_PRODUCT", id);
    }
    public List<String> searchProducts(String query) {
        return cache.entrySet().stream()
            .filter(e -> e.getKey().startsWith("prod:"))
            .filter(e -> e.getValue().toString().toLowerCase().contains(query.toLowerCase()))
            .map(e -> e.getKey().substring(5))
            .collect(Collectors.toList());
    }

    // --- Order Methods ---
    public String createOrder(String userId, List<String> productIds) {
        String orderId = UUID.randomUUID().toString();
        List<Map<String, Object>> items = productIds.stream()
            .map(pid -> getProduct(pid).orElse(null))
            .filter(Objects::nonNull)
            .collect(Collectors.toList());
        double total = items.stream()
            .mapToDouble(p -> (Double) p.get("price"))
            .sum();
        cache.put("order:" + orderId, Map.of(
            "userId", userId, "items", items, "total", total, "status", "PENDING"
        ));
        logEvent("CREATE_ORDER", orderId);
        return orderId;
    }
    public boolean confirmOrder(String orderId) {
        Object o = cache.get("order:" + orderId);
        if (!(o instanceof Map)) return false;
        Map<String, Object> order = new HashMap<>((Map<String, Object>) o);
        order.put("status", "CONFIRMED");
        cache.put("order:" + orderId, order);
        logEvent("CONFIRM_ORDER", orderId);
        return true;
    }
    public boolean cancelOrder(String orderId) {
        Object o = cache.get("order:" + orderId);
        if (!(o instanceof Map)) return false;
        Map<String, Object> order = new HashMap<>((Map<String, Object>) o);
        if ("COMPLETED".equals(order.get("status"))) return false;
        order.put("status", "CANCELLED");
        cache.put("order:" + orderId, order);
        logEvent("CANCEL_ORDER", orderId);
        return true;
    }
    public Optional<Map<String, Object>> getOrder(String orderId) {
        Object v = cache.get("order:" + orderId);
        return v instanceof Map ? Optional.of((Map<String, Object>) v) : Optional.empty();
    }
    public List<String> getUserOrders(String userId) {
        return cache.entrySet().stream()
            .filter(e -> e.getKey().startsWith("order:"))
            .filter(e -> {
                Object o = e.getValue();
                return o instanceof Map && userId.equals(((Map<?, ?>) o).get("userId"));
            })
            .map(e -> e.getKey().substring(6))
            .collect(Collectors.toList());
    }

    // --- User Methods ---
    public void registerUser(String id, String name, String email) {
        cache.put("user:" + id, Map.of("name", name, "email", email, "active", true));
        logEvent("REGISTER_USER", id);
    }
    public Optional<Map<String, Object>> getUser(String id) {
        Object v = cache.get("user:" + id);
        return v instanceof Map ? Optional.of((Map<String, Object>) v) : Optional.empty();
    }
    public boolean deactivateUser(String id) {
        return getUser(id).map(u -> {
            Map<String, Object> updated = new HashMap<>(u);
            updated.put("active", false);
            cache.put("user:" + id, updated);
            logEvent("DEACTIVATE_USER", id);
            return true;
        }).orElse(false);
    }
    public boolean updateEmail(String id, String newEmail) {
        if (!newEmail.contains("@")) return false;
        return getUser(id).map(u -> {
            Map<String, Object> updated = new HashMap<>(u);
            updated.put("email", newEmail);
            cache.put("user:" + id, updated);
            logEvent("UPDATE_EMAIL", id);
            return true;
        }).orElse(false);
    }
    public List<String> getActiveUsers() {
        return cache.entrySet().stream()
            .filter(e -> e.getKey().startsWith("user:"))
            .filter(e -> e.getValue() instanceof Map && Boolean.TRUE.equals(((Map<?, ?>) e.getValue()).get("active")))
            .map(e -> e.getKey().substring(5))
            .collect(Collectors.toList());
    }

    // --- Analytics ---
    public Map<String, Integer> getProductStats() {
        Map<String, Integer> stats = new HashMap<>();
        stats.put("total", (int) cache.keySet().stream().filter(k -> k.startsWith("prod:")).count());
        stats.put("outOfStock", (int) cache.entrySet().stream()
            .filter(e -> e.getKey().startsWith("prod:"))
            .filter(e -> e.getValue() instanceof Map && ((Integer)((Map<?,?>)e.getValue()).get("qty")) == 0)
            .count());
        return stats;
    }
    public double getTotalRevenue() {
        return cache.entrySet().stream()
            .filter(e -> e.getKey().startsWith("order:"))
            .filter(e -> e.getValue() instanceof Map && "COMPLETED".equals(((Map<?,?>) e.getValue()).get("status")))
            .mapToDouble(e -> (Double) ((Map<?,?>) e.getValue()).get("total"))
            .sum();
    }
    public Map<String, Long> getOrdersByStatus() {
        return cache.entrySet().stream()
            .filter(e -> e.getKey().startsWith("order:"))
            .filter(e -> e.getValue() instanceof Map)
            .collect(Collectors.groupingBy(
                e -> (String) ((Map<?,?>) e.getValue()).get("status"),
                Collectors.counting()
            ));
    }

    // --- Logging ---
    private void logEvent(String type, String detail) {
        log.add(System.currentTimeMillis() + "|" + type + "|" + detail);
    }
    public List<String> getLog() { return Collections.unmodifiableList(log); }
    public void clearLog() { log.clear(); }
    public List<String> getLogByType(String type) {
        return log.stream().filter(l -> l.contains("|" + type + "|")).collect(Collectors.toList());
    }
}
