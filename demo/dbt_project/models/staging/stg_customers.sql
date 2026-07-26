with source as (
    select * from {{ ref('raw_customers') }}
),

staged as (
    select
        customer_id,
        name,
        lower(email)                as email,
        country,
        lower(plan)                 as plan,
        cast(created_at as date)    as created_at,
        lifetime_orders,
        -- Derived segments
        case
            when plan = 'enterprise' then 'high'
            when plan = 'pro'        then 'medium'
            else                         'low'
        end                         as value_segment
    from source
)

select * from staged
