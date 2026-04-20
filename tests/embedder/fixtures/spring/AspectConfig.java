package com.example.aspect;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.*;
import org.springframework.stereotype.Component;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
@Aspect
@Component
public class AspectConfig {
    private static final Logger log = LoggerFactory.getLogger(AspectConfig.class);
    @Pointcut("execution(* com.example.service.*.*(..))")
    public void serviceLayer() {}
    @Before("serviceLayer()")
    public void logBefore() { log.info("Before service call"); }
    @After("serviceLayer()")
    public void logAfter() { log.info("After service call"); }
    @Around("serviceLayer()")
    public Object logAround(ProceedingJoinPoint jp) throws Throwable {
        long start = System.currentTimeMillis();
        try {
            Object result = jp.proceed();
            log.info("Method {} took {}ms", jp.getSignature().getName(), System.currentTimeMillis() - start);
            return result;
        } catch (Exception e) {
            log.error("Exception in {}: {}", jp.getSignature().getName(), e.getMessage());
            throw e;
        }
    }
    @AfterThrowing(pointcut = "serviceLayer()", throwing = "ex")
    public void logException(Exception ex) { log.error("Exception: {}", ex.getMessage()); }
    @AfterReturning(pointcut = "serviceLayer()", returning = "result")
    public void logReturn(Object result) { log.debug("Returned: {}", result); }
}
