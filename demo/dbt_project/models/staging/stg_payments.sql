with source as (
    select * from {{ ref('raw_payments') }}
),

staged as (
    select
        payment_id,
        order_id,
        lower(payment_method)       as payment_method,
        cast(amount as decimal(10, 2)) as amount,
        lower(status)               as status,
        cast(payment_date as date)  as payment_date,
        -- Derived
        case
            when status = 'success' then true
            else false
        end                         as is_successful
    from source
)

select * from staged
