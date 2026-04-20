package com.example.config;
import org.springframework.context.annotation.*;
import org.springframework.boot.autoconfigure.condition.*;
@Configuration
public class ConditionalBeanConfig {
    @Bean
    @Profile("production")
    public String productionDataSource() { return "prod-datasource"; }
    @Bean
    @Profile("development")
    public String devDataSource() { return "dev-datasource"; }
    @Bean
    @ConditionalOnProperty(name = "feature.cache.enabled", havingValue = "true")
    public String cacheManager() { return "cache-manager"; }
    @Bean
    @ConditionalOnMissingBean
    public String defaultService() { return "default-service"; }
    @Bean
    public String optionalService() { return "optional-service"; }
}
