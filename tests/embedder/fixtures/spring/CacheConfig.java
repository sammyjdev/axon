package com.example.config;
import org.springframework.cache.CacheManager;
import org.springframework.cache.annotation.*;
import org.springframework.context.annotation.*;
import org.springframework.cache.concurrent.ConcurrentMapCacheManager;
@Configuration
@EnableCaching
public class CacheConfig {
    @Bean
    public CacheManager cacheManager() { return new ConcurrentMapCacheManager("users", "products"); }
    @Cacheable("users")
    public String getUser(Long id) { return "user-" + id; }
    @CachePut(value = "users", key = "#id")
    public String updateUser(Long id, String name) { return name; }
    @CacheEvict(value = "users", key = "#id")
    public void deleteUser(Long id) { System.out.println("Evicted user: " + id); }
    @CacheEvict(value = "users", allEntries = true)
    public void clearAllUsers() { System.out.println("Cleared all users"); }
}
