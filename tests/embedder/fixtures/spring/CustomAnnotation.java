package com.example.annotation;
import java.lang.annotation.*;
@Retention(RetentionPolicy.RUNTIME)
@Target({ElementType.METHOD, ElementType.TYPE})
@Documented
public @interface CustomAnnotation {
    String value() default "";
    String description() default "";
    boolean required() default true;
    int timeout() default 30;
    Class<?>[] groups() default {};
}
