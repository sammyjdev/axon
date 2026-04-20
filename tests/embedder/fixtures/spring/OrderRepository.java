package com.example.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import java.util.List;
import java.util.Optional;

public interface OrderRepository extends JpaRepository<Object, Long> {
    Optional<Object> findByOrderNumber(String orderNumber);
    List<Object> findByUserId(Long userId);
    @Query("SELECT o FROM Order o WHERE o.status = :status")
    List<Object> findByStatus(String status);
    @Query("SELECT o FROM Order o WHERE o.total > :amount")
    List<Object> findByTotalGreaterThan(double amount);
    long countByUserId(Long userId);
}
